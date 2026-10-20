"""Numeric Preview stage timings only: never prompts, credentials or asset URLs."""
import json
import logging
import re
import time

LOGGER=logging.getLogger('uvicorn.error.preview_illustration_timing')
LOGGER.setLevel(logging.INFO)  # Reuse Uvicorn's existing stderr/journal handler.

def elapsed(phase,started,request_id='',status='ok'):
    milliseconds=round(max(0,time.perf_counter()-started)*1000,3)
    safe_id=request_id if re.fullmatch(r'[a-zA-Z0-9_-]{1,100}',request_id or '') and not re.search('sk-|secret|token|key',request_id,re.I) else ''
    LOGGER.info('preview_illustration_timing %s',json.dumps({'phase':phase,'duration_ms':milliseconds,'status':status,'request_id':safe_id},separators=(',',':')))
    return milliseconds
