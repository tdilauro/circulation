try:
    import wrapt
except Exception as e:
    print e

# This file hooks the import of some of the simplified base classes so that when they are imported
# they are patched. This uses the methods defined in the wrapt module to do so. This blog post gives
# good background on these methods: http://blog.dscpl.com.au/2015/03/ordering-issues-when-monkey-patching-in.html
#
# This file is loaded in before any of the simplified code using this method:
# https://stackoverflow.com/questions/40484942/how-to-execute-python-code-on-interpreter-startup-in-virtualenv
# basically a pth file is added to the virtualenv path that loads these functions so they are always loaded
# before any other simplified code, letting us hook our profiling code in.


_PATCHED_SCRIPTS = False


@wrapt.when_imported('core.scripts')
def patch_scripts(module):
    global _PATCHED_SCRIPTS
    if _PATCHED_SCRIPTS:
        return
    _PATCHED_SCRIPTS = True
    from lyrasis.util import setup_xray
    from lyrasis.scripts import patch as do_patch_scripts
    setup_xray()
    do_patch_scripts(module)


_PATCHED_APP = False


@wrapt.when_imported('api.app')
def patch_app(module):
    global _PATCHED_APP
    if _PATCHED_APP:
        return
    _PATCHED_APP = True
    from lyrasis.util import setup_xray
    from lyrasis.middleware import LyrasisXRayMiddleware
    from aws_xray_sdk.core import xray_recorder
    setup_xray()
    LyrasisXRayMiddleware(module.app, xray_recorder)