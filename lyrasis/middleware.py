from aws_xray_sdk.core.models import http
from aws_xray_sdk.core.utils import stacktrace
from aws_xray_sdk.ext.flask.middleware import _patch_render
from aws_xray_sdk.ext.util import \
    construct_xray_header, \
    calculate_segment_name, \
    calculate_sampling_decision, prepare_response_header
from flask import request


class LyrasisXRayMiddleware(object):

    def __init__(self, app, recorder, headers=False):
        self.app = app
        self.app.logger.info("initializing lyrasis xray middleware")
        self._recorder = recorder
        self._first_request = False
        self._xray_header = headers

        # Directly manipulate the app to make sure these functions
        # are inserted first, so they run in the correct order
        self.app.before_first_request_funcs.insert(0, self._before_first_request)
        self.app.before_request_funcs.setdefault(None, []).insert(0, self._before_request)
        self.app.after_request_funcs.setdefault(None, []).insert(0, self._after_request)
        self.app.teardown_request_funcs.setdefault(None, []).insert(0, self._teardown_request)
        self.app.teardown_appcontext_funcs.insert(0, self._teardown_appcontext)

        _patch_render(recorder)

    def _before_first_request(self):
        self._before_request()
        segment = self._recorder.current_segment()
        segment.put_annotation("request", "first")
        self._first_request = True

    def _before_request(self):
        if self._first_request:
            self._first_request = False
            return

        headers = request.headers
        xray_header = construct_xray_header(headers)
        req = request._get_current_object()

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

        segment.save_origin_trace_header(xray_header)
        segment.put_http_meta(http.URL, req.base_url)
        segment.put_http_meta(http.METHOD, req.method)
        segment.put_http_meta(http.USER_AGENT, headers.get('User-Agent'))

        client_ip = headers.get('X-Forwarded-For') or headers.get('HTTP_X_FORWARDED_FOR')
        if client_ip:
            segment.put_http_meta(http.CLIENT_IP, client_ip)
            segment.put_http_meta(http.X_FORWARDED_FOR, True)
        else:
            segment.put_http_meta(http.CLIENT_IP, req.remote_addr)

    def _after_request(self, response):
        segment = self._recorder.current_segment()
        segment.put_http_meta(http.STATUS, response.status_code)

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
        if not segment:
            return

        segment.put_http_meta(http.STATUS, 500)
        stack = stacktrace.get_stacktrace(limit=self._recorder._max_trace_back)
        segment.add_exception(exception, stack)

    def _teardown_appcontext(self, exception):
        segment = self._recorder.current_segment()
        self._recorder.end_segment()
