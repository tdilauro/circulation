import os
from core.config import Configuration
from lyrasis.sqlalchemy import patch as sqlalchemy_patch
from aws_xray_sdk.core import xray_recorder, patch as xray_patch


_XRAY_SETUP = False


def put_annotations(segment, seg_type=None):
    if type is not None:
        segment.put_annotation('type', seg_type)

    for env in os.environ.keys():
        if env.startswith('XRAY_ANNOTATE_'):
            name = env.replace('XRAY_ANNOTATE_', '').lower()
            segment.put_annotation(name, os.environ[env])

    if Configuration.app_version() != Configuration.NO_APP_VERSION_FOUND:
        segment.put_annotation('version', Configuration.app_version())


def setup_xray():
    # make sure this only gets called once
    global _XRAY_SETUP
    if _XRAY_SETUP:
        return
    xray_recorder.configure(service="SimplyE", streaming_threshold=5, context_missing='LOG_ERROR')
    sqlalchemy_patch()
    xray_patch(['requests', 'httplib'])
    _XRAY_SETUP = True
