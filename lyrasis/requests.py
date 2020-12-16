import wrapt

from aws_xray_sdk.core import xray_recorder
from aws_xray_sdk.ext.util import get_hostname
from aws_xray_sdk.ext.requests.patch import _inject_header, requests_processor
from fnmatch import fnmatch


def patch():

    wrapt.wrap_function_wrapper(
        'requests',
        'Session.request',
        _xray_traced_requests
    )

    wrapt.wrap_function_wrapper(
        'requests',
        'Session.prepare_request',
        _inject_header
    )


def _xray_traced_requests(wrapped, instance, args, kwargs):
    global EXCLUDED_HOSTNAMES
    url = kwargs.get('url') or args[1]
    hostname = get_hostname(url)
    for exclude_pattern in EXCLUDED_HOSTNAMES:
        if fnmatch(hostname, exclude_pattern):
            return wrapped(*args, **kwargs)

    return xray_recorder.record_subsegment(
        wrapped, instance, args, kwargs,
        name=hostname,
        namespace='remote',
        meta_processor=requests_processor,
    )


EXCLUDED_HOSTNAMES = ['logs.*.amazonaws.com']