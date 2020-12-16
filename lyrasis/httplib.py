from collections import namedtuple
import sys
import wrapt

import urllib3.connection

from aws_xray_sdk.core import xray_recorder
from aws_xray_sdk.core.exceptions.exceptions import SegmentNotFoundException
from aws_xray_sdk.ext.httplib.patch import (_xray_traced_http_getresponse,
    _xray_traced_http_client_read,
    http_send_request_processor,
    _XRAY_PROP,
    _XRay_Data,
)
from aws_xray_sdk.ext.util import inject_trace_header, strip_url, unwrap, get_hostname

if sys.version_info >= (3, 0, 0):
    PY2 = False
    httplib_client_module = 'http.client'
    import http.client as httplib
else:
    PY2 = True
    httplib_client_module = 'httplib'
    import httplib

_xray_httplib_ignored = namedtuple('xray_httplib_ignored', ['subclass', 'hostname', 'urls'])


IGNORED_REQUESTS = []


def add_ignore(subclass=None, host=None, urls=None):
    global IGNORED_REQUESTS
    if subclass is not None or host is not None or urls is not None:
        IGNORED_REQUESTS.append(_xray_httplib_ignored(subclass=subclass, host=host, urls=urls))


# skip httplib tracing for SDK built-in centralized sampling pollers
add_ignore(subclass='botocore.awsrequest.AWSHTTPConnection', urls=['/GetSamplingRules', '/SamplingTargets'])


def _is_ignored(instance, url):
    global IGNORED_REQUESTS
    for rule in IGNORED_REQUESTS:
        if rule.subclass is None:
            subclass_match = True
        else:
            subclass_match = type(instance).__name__ == rule.subclass

        if rule.host is None:
            host_match = True
        else:
            host_match = instance.host == rule.host

        if rule.urls is None:
            url_match = True
        else:
            url_match = False
            for url_rule in rule.urls:
                url_match |= url_rule == url

        if url_match and host_match and subclass_match:
            return True
    return False


def _send_request(wrapped, instance, args, kwargs):
    def decompose_args(method, url, body, headers, encode_chunked=False):
        if _is_ignored(instance, url):
            return wrapped(*args, **kwargs)

        # Only injects headers when the subsegment for the outgoing
        # calls are opened successfully.
        subsegment = None
        try:
            subsegment = xray_recorder.current_subsegment()
        except SegmentNotFoundException:
            pass
        if subsegment:
            inject_trace_header(headers, subsegment)

        if issubclass(instance.__class__, urllib3.connection.HTTPSConnection):
            ssl_cxt = getattr(instance, 'ssl_context', None)
        elif issubclass(instance.__class__, httplib.HTTPSConnection):
            ssl_cxt = getattr(instance, '_context', None)
        else:
            # In this case, the patcher can't determine which module the connection instance is from.
            # We default to it to check ssl_context but may be None so that the default scheme would be
            # (and may falsely be) http.
            ssl_cxt = getattr(instance, 'ssl_context', None)
        scheme = 'https' if ssl_cxt and type(ssl_cxt).__name__ == 'SSLContext' else 'http'
        xray_url = '{}://{}{}'.format(scheme, instance.host, url)
        xray_data = _XRay_Data(method, instance.host, xray_url)
        setattr(instance, _XRAY_PROP, xray_data)

        # we add a segment here in case connect fails
        return xray_recorder.record_subsegment(
            wrapped, instance, args, kwargs,
            name=get_hostname(xray_data.url),
            namespace='remote',
            meta_processor=http_send_request_processor
        )

    return decompose_args(*args, **kwargs)


def patch():
    wrapt.wrap_function_wrapper(
        httplib_client_module,
        'HTTPConnection._send_request',
        _send_request
    )

    wrapt.wrap_function_wrapper(
        httplib_client_module,
        'HTTPConnection.getresponse',
        _xray_traced_http_getresponse
    )

    wrapt.wrap_function_wrapper(
        httplib_client_module,
        'HTTPResponse.read',
        _xray_traced_http_client_read
    )

