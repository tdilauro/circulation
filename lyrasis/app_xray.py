import os
import sys

from api.app import app
from core.model import SessionManager
from core.config import Configuration
from lyrasis.middleware import LyrasisXRayMiddleware
from lyrasis.monkeypatch import monkeypatch_method, monkeypatch_classmethod
from aws_xray_sdk.core import xray_recorder, patch as xray_patch, patch_all as xray_patch_all
from aws_xray_sdk.ext.sqlalchemy.util import decorators


# this needs to be patched because of this bug that isn't in a release yet:
# https://github.com/aws/aws-xray-sdk-python/pull/234
# this also goes beyond that patch to add stack traces to the query record
# if that proves useful it should be contributed back upstream
@monkeypatch_method(decorators)
def xray_on_call(cls, func):
    def wrapper(*args, **kw):
        from aws_xray_sdk.core.utils import stacktrace
        from aws_xray_sdk.ext.sqlalchemy.query import XRayQuery, XRaySession
        from aws_xray_sdk.ext.util import strip_url
        from sqlalchemy.orm.exc import NoResultFound, MultipleResultsFound
        class_name = str(cls.__module__)
        c = xray_recorder._context
        sql = None
        subsegment = None
        # if class_name == "sqlalchemy.orm.session":
        #     for arg in args:
        #         if isinstance(arg, XRaySession):
        #             sql = decorators.parse_bind(arg.bind)
        if class_name == 'sqlalchemy.orm.query':
            for arg in args:
                if isinstance(arg, XRayQuery):
                    try:
                        sql = decorators.parse_bind(arg.session.bind)
                        if xray_recorder.stream_sql:
                            sql['sanitized_query'] = str(arg)
                    except Exception:
                        sql = None
        if sql is not None:
            if getattr(c._local, 'entities', None) is not None:
                # Strip URL of ? and following text
                sub_name = strip_url(sql['url'])
                subsegment = xray_recorder.begin_subsegment(sub_name, namespace='remote')
        try:
            res = func(*args, **kw)
        except:
            exception = sys.exc_info()[1]
            if subsegment is not None and exception.__class__ not in [NoResultFound, MultipleResultsFound]:
                exception = sys.exc_info()[1]
                stack = stacktrace.get_stacktrace(limit=xray_recorder._max_trace_back)
                subsegment.add_exception(exception, stack)
            raise
        finally:
            if subsegment is not None:
                subsegment.set_sql(sql)
                subsegment.put_annotation("sqlalchemy", class_name + '.' + func.__name__)
                xray_recorder.end_subsegment()
        return res
    return wrapper


@monkeypatch_classmethod(SessionManager)
def sessionmaker(cls, url=None, session=None):
    from aws_xray_sdk.ext.sqlalchemy.query import XRaySessionMaker
    if not (url or session):
        url = Configuration.database_url()
    if url:
        bind_obj = cls.engine(url)
    elif session:
        bind_obj = session.get_bind()
        if not os.environ.get('TESTING'):
            # If a factory is being created from a session in test mode,
            # use the same Connection for all of the tests so objects can
            # be accessed. Otherwise, bind against an Engine object.
            bind_obj = bind_obj.engine
    return XRaySessionMaker(bind=bind_obj)


# Add the xray middleware to the Simply-E app
xray_recorder.configure(service="SimplyE", dynamic_naming="*", streaming_threshold=0)
xray_middleware = LyrasisXRayMiddleware(app, xray_recorder)
xray_patch(['requests', 'httplib'])