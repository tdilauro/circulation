from aws_xray_sdk.core.models import http
from aws_xray_sdk.core.utils import stacktrace
from aws_xray_sdk.ext.flask.middleware import _patch_render
from aws_xray_sdk.ext.util import \
    construct_xray_header, \
    calculate_segment_name, \
    calculate_sampling_decision, prepare_response_header
from flask import request, session
from lyrasis.util import put_annotations
import os

# Define our own middleware class for profiling with AWS xray. This is heavily based on
# https://github.com/aws/aws-xray-sdk-python/blob/master/aws_xray_sdk/ext/flask/middleware.py
# but we make our own class because that one assumes all requests to the database happen in the main
# flask request. In the case of Simplified this is not true, requests to the database happen before and during
# teardown, so this class starts a segment earlier and ends it later. Additionally this class takes advantage
# of some Simplified specific information in the request, so that we gather more data about the library and
# use the request is coming from.


class LyrasisXRayMiddleware(object):

    def __init__(self, app, recorder, headers=False):
        self.app = app
        self.app.logger.info("initializing lyrasis xray middleware")
        self._recorder = recorder
        self._first_request = False
        self._xray_header = headers

        # Directly manipulate the app lists to make sure these functions are inserted first,
        # so they run before any of the simplified functions.
        self.app.before_first_request_funcs.insert(0, self._before_first_request)
        self.app.before_request_funcs.setdefault(None, []).insert(0, self._before_request)
        self.app.after_request_funcs.setdefault(None, []).insert(0, self._after_request)
        self.app.teardown_request_funcs.setdefault(None, []).insert(0, self._teardown_request)
        self.app.teardown_appcontext_funcs.insert(0, self._teardown_appcontext)

        _patch_render(recorder)

    def _before_first_request(self):
        self._before_request()
        segment = self._recorder.current_segment()
        if segment is not None:
            # Add an annotation for the first request, since it does extra caching work.
            segment.put_annotation("request", "first")

    def _before_request(self):
        headers = request.headers
        xray_header = construct_xray_header(headers)
        req = request._get_current_object()

        # Let us define our own service name with an ENV variable
        if 'XRAY_SERVICE_NAME' in os.environ:
            name = os.environ['XRAY_SERVICE_NAME']
        else:
            name = calculate_segment_name(req.host, self._recorder)

        sampling_req = {
            'host': req.host,
            'method': req.method,
            'path': req.path,
            'service': name,
        }
        sampling_decision = calculate_sampling_decision(
            trace_header=xray_header,
            recorder=self._recorder,
            sampling_req=sampling_req,
        )
        segment = self._recorder.begin_segment(
            name=name,
            traceid=xray_header.root,
            parent_id=xray_header.parent,
            sampling=sampling_decision,
        )
        if segment is not None:
            segment.save_origin_trace_header(xray_header)
            segment.put_http_meta(http.URL, req.base_url)
            segment.put_http_meta(http.METHOD, req.method)
            segment.put_http_meta(http.USER_AGENT, headers.get('User-Agent'))

            # This allows us to add annotations based on some environment variables
            put_annotations(segment, 'web')

            client_ip = headers.get('X-Forwarded-For') or headers.get('HTTP_X_FORWARDED_FOR')
            if client_ip:
                segment.put_http_meta(http.CLIENT_IP, client_ip)
                segment.put_http_meta(http.X_FORWARDED_FOR, True)
            else:
                segment.put_http_meta(http.CLIENT_IP, req.remote_addr)

    def _after_request(self, response):
        segment = self._recorder.current_segment()
        if segment is not None:
            segment.put_http_meta(http.STATUS, response.status_code)

            # Add library shortname
            if hasattr(request, 'library'):
                segment.put_annotation('library', str(request.library.short_name))

            # Add patron data
            if hasattr(request, 'patron'):
                segment.set_user(str(request.patron.authorization_identifier))
                segment.put_annotation('barcode', str(request.patron.authorization_identifier))

            # Add admin UI username
            if 'admin_email' in session:
                segment.set_user(session['admin_email'])

            if self._xray_header:
                origin_header = segment.get_origin_trace_header()
                resp_header_str = prepare_response_header(origin_header, segment)
                response.headers[http.XRAY_HEADER] = resp_header_str

            cont_len = response.headers.get('Content-Length')
            if cont_len:
                segment.put_http_meta(http.CONTENT_LENGTH, int(cont_len))

        return response

    def _teardown_request(self, exception):
        if not exception:
            return
        segment = None
        try:
            segment = self._recorder.current_segment()
        except Exception:
            pass
        if segment is not None:
            segment.put_http_meta(http.STATUS, 500)
            stack = stacktrace.get_stacktrace(limit=self._recorder._max_trace_back)
            segment.add_exception(exception, stack)

    def _teardown_appcontext(self, exception):
        self._recorder.end_segment()
