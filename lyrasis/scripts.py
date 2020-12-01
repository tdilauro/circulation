from aws_xray_sdk.core import xray_recorder
from aws_xray_sdk.core.utils import stacktrace
import os
from lyrasis.util import put_annotations
import sys
import wrapt

# These are the patches we apply to the scripts classes in order to capture traces from the
# scripts. These start a segment whenever the _db property is accessed or the run function is called
# whichever happens first. It ends the trace after the run function ends.


def _start_segment(instance):
    if not getattr(instance, "_segment_started", False):
        if 'XRAY_SERVICE_NAME' in os.environ:
            name = os.environ['XRAY_SERVICE_NAME']
        else:
            name = instance.script_name
        segment = xray_recorder.begin_segment(name)
        put_annotations(segment, 'script')
        segment.put_annotation('script', instance.script_name)
        instance._segment_started = True


def _xray_traced_script_run(wrapped, instance, args, kwargs):
    _start_segment(instance)
    segment = xray_recorder.current_segment()
    # If we are in a script with a lot of providers then use the service name
    if hasattr(instance, 'providers'):
        if len(instance.providers) > 0:
            segment.put_annotation('script', str(instance.providers[0].service_name))
    try:
        res = wrapped(*args, **kwargs)
    except:
        exception = sys.exc_info()[1]
        stack = stacktrace.get_stacktrace(limit=xray_recorder._max_trace_back)
        segment.add_exception(exception, stack)
        raise
    finally:
        xray_recorder.end_segment()
    return res


def _xray_traced_script_db(self):
    # Start segment
    _start_segment(self)
    # Call the original function
    return self._db_before_patch


def patch(module):
    # This function does the actual patching of the scripts clases
    module.Script._db_before_patch = module.Script._db
    module.Script._db = property(_xray_traced_script_db)
    wrapt.wrap_function_wrapper(
        module,
        'Script.run',
        _xray_traced_script_run
    )
