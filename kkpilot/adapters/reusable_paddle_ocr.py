"""Lazy warm OCR per extractor; bounded reads and fail-closed recovery."""
import atexit
import copy
import hashlib
import threading
import time
from . import local_paddle_ocr as cold
from .warm_paddle_session import WarmOCRSession, WarmOCRError


class ReusableOCR:
    def __init__(self, config, *, session_factory=None, clock=None, session_max_requests=6):
        if type(session_max_requests) is not int or not 1 <= session_max_requests <= 10000:
            raise ValueError('OCR_SESSION_LIMIT_INVALID')
        self.config = copy.deepcopy(config)
        self._factory = session_factory or WarmOCRSession
        self._clock = clock or time.perf_counter
        self._session = None
        self._session_limit = session_max_requests
        self._degraded = False
        self._closed = False
        self._lock = threading.Lock()
        self._last = {}
        atexit.register(self.close)

    def run(self, image, config, capture=None):
        if config != self.config:
            raise ValueError('OCR_CONFIG_CHANGED')
        if type(image) is not bytes:
            raise ValueError('OCR_IMAGE_INVALID')
        digest = hashlib.sha256(image).hexdigest()
        if not self._lock.acquire(blocking=False):
            raise ValueError('OCR_CONCURRENCY_LIMIT')
        started = self._clock()
        try:
            if self._closed:
                raise ValueError('OCR_RUNTIME_CLOSED')
            if self._degraded:
                # Only a later invocation uses cold OCR; never replay a failed request here.
                if self._session is not None:
                    self._session.close()
                    self._session = None
                raw = cold.run(image, config)
                self._last = {'image_sha256': digest, 'local_ocr_runtime': 'COLD_FALLBACK'}
            else:
                try:
                    startup = 0.0
                    if self._session is not None and self._session.last_metadata.get('request_index', 0) >= self._session_limit:
                        self._session.close()
                        self._session = None
                    if self._session is None:
                        self._session = self._factory(config, startup_timeout_seconds=config['timeout_seconds'])
                        self._session.start()
                        startup = self._clock() - started
                    remaining = config['timeout_seconds'] - (self._clock() - started)
                    if remaining <= 0:
                        raise WarmOCRError('WARM_DEADLINE')
                    raw = self._session.run(image, timeout_seconds=remaining)
                    self._last = {'image_sha256': digest, 'local_ocr_runtime': 'WARM_SESSION',
                                  'local_ocr_startup_seconds': startup,
                                  'local_ocr_session_request_index': self._session.last_metadata['request_index']}
                except Exception:
                    self._degraded = True
                    self._last = {'image_sha256': digest, 'local_ocr_runtime': 'WARM_FAILED'}
                    session = self._session
                    if session is not None:
                        session.close()
                        self._session = None
                    raise
            if capture is not None:
                capture(raw)
            return raw
        finally:
            self._lock.release()

    def metadata_for(self, image):
        # A concurrent/failed request must not inherit another image's diagnostics.
        return self.metadata_for_digest(hashlib.sha256(image).hexdigest())

    def metadata_for_digest(self, digest):
        value = dict(self._last)
        if value.pop('image_sha256', None) != digest:
            return {}
        return value

    def close(self):
        with self._lock:
            self._closed = True
            session, self._session = self._session, None
            if session is not None:
                session.close()
