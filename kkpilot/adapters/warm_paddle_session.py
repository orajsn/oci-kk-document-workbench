"""Opt-in reusable, network-isolated CPU OCR session. No automatic retries.

Native process startup/send/read have deadlines. Parent-side manifest validation
and filesystem operations do not have independent hard wall-clock deadlines.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import signal
import subprocess
import tempfile
import threading
import time
import uuid

from . import local_paddle_ocr as cold
from .warm_paddle_worker import VERSION, MAX_IMAGE, MAX_OCR_OUTPUT, MAX_PACKET, MAX_REQUEST


class WarmOCRError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _need(value, code):
    if not value:
        raise WarmOCRError(code)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'), sort_keys=True).encode()


def _seconds(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 86400


class WarmOCRSession:
    def __init__(self, config, startup_timeout_seconds=90):
        _need(type(config) is dict, 'WARM_CONFIG')
        _need(type(startup_timeout_seconds) in (int, float) and math.isfinite(startup_timeout_seconds)
              and 0 < startup_timeout_seconds <= 180, 'WARM_STARTUP_TIMEOUT')
        _need(type(config.get('timeout_seconds')) is int and 1 <= config['timeout_seconds'] <= 60, 'WARM_READ_TIMEOUT')
        _need(config.get('enabled') is True, 'WARM_DISABLED')
        self.config = copy.deepcopy(config)
        self._timeout_seconds = config['timeout_seconds']
        self.startup_timeout_seconds = startup_timeout_seconds
        self.last_metadata = {'status': 'NEW'}
        self._process = None
        self._temporary = None
        self._reader = None
        self._writer = None
        self._packets = queue.Queue(maxsize=2)
        self._reader_error = None
        self._lock = threading.Lock()
        self._state = 'NEW'
        self._owns_slot = False
        self._request_index = 0
        self._startup = {}

    def __enter__(self):
        return self.start()

    def __exit__(self, kind, value, traceback):
        self.close()

    def _launch_worker(self):
        _need(os.name == 'posix', 'WARM_PLATFORM')
        cold.check_resource_limits()
        cold.verify_models(self.config['model_manifest_path'], self.config['model_manifest_sha256'])
        self._temporary = tempfile.TemporaryDirectory(prefix='kk-warm-ocr-')
        directory = self._temporary.name
        os.chmod(directory, 0o700)
        env = {'PATH': '/usr/bin:/bin', 'TMPDIR': directory, 'LD_LIBRARY_PATH': str(cold.NATIVE),
               'PADDLE_PDX_CACHE_HOME': directory, 'PADDLE_HOME': directory, 'XDG_CACHE_HOME': directory, 'HF_HOME': directory,
               'PYTHONNOUSERSITE': '1', 'PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK': 'True',
               'DISABLE_MODEL_SOURCE_CHECK': 'True', 'OMP_NUM_THREADS': '2',
               'OPENBLAS_NUM_THREADS': '2', 'MKL_NUM_THREADS': '2', 'GLOG_minloglevel': '3'}
        command = ['/usr/bin/unshare', '--user', '--map-root-user', '--net', str(cold.PYTHON), '-I', '-B',
                   str(Path(__file__).with_name('warm_paddle_worker.py').resolve()), str(cold.DET), str(cold.REC),
                   os.readlink('/proc/self/ns/net')]
        return subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                bufsize=0, env=env, start_new_session=True)

    def _read_loop(self):
        def failed(code):
            self._reader_error = code
            try:
                self._packets.put_nowait(None)
            except queue.Full:
                pass
        try:
            while True:
                raw = self._process.stdout.readline(MAX_PACKET + 1)
                if not raw:
                    self._packets.put_nowait(None)
                    return
                if len(raw) > MAX_PACKET or not raw.endswith(b'\n'):
                    failed('WARM_PACKET_LIMIT')
                    return
                self._packets.put_nowait(raw)
        except queue.Full:
            failed('WARM_UNSOLICITED_OUTPUT')
        except Exception:
            failed('WARM_READ_FAILED')

    def _packet(self, deadline):
        _need(self._reader_error is None, self._reader_error or 'WARM_READ_FAILED')
        remaining = deadline - time.perf_counter()
        _need(remaining > 0, 'WARM_DEADLINE')
        try:
            raw = self._packets.get(timeout=remaining)
        except queue.Empty:
            raise WarmOCRError('WARM_DEADLINE') from None
        _need(self._reader_error is None, self._reader_error or 'WARM_READ_FAILED')
        _need(raw is not None, 'WARM_WORKER_EXITED')
        def unique(items):
            result = {}
            for key, value in items:
                _need(key not in result, 'WARM_PACKET_JSON')
                result[key] = value
            return result
        try:
            value = json.loads(raw, object_pairs_hook=unique,
                               parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except Exception:
            raise WarmOCRError('WARM_PACKET_JSON') from None
        _need(type(value) is dict and value.get('protocol') == VERSION, 'WARM_PROTOCOL')
        _need(value.get('type') != 'failed', 'WARM_WORKER_FAILED')
        return value, len(raw)

    def start(self):
        _need(self._state == 'NEW', 'WARM_SESSION_NOT_NEW')
        _need(cold._OCR_SEMAPHORE.acquire(blocking=False), 'OCR_CONCURRENCY_LIMIT')
        self._owns_slot = True
        self._state = 'STARTING'
        try:
            started = time.perf_counter()
            self._process = self._launch_worker()
            self._reader = threading.Thread(target=self._read_loop, name='kk-warm-ocr-reader', daemon=True)
            self._reader.start()
            ready, _ = self._packet(started + self.startup_timeout_seconds)
            _need(set(ready) == {'type', 'protocol', 'pid', 'library_import_seconds', 'model_initialization_seconds'}
                  and ready['type'] == 'ready' and type(ready['pid']) is int and ready['pid'] == self._process.pid
                  and _seconds(ready['library_import_seconds']) and _seconds(ready['model_initialization_seconds']), 'WARM_READY_BINDING')
            self._startup = {'startup_seconds': time.perf_counter() - started,
                             'library_import_seconds': ready['library_import_seconds'],
                             'model_initialization_seconds': ready['model_initialization_seconds']}
            self._state = 'READY'
            self.last_metadata = {'status': 'READY', 'model_initializations': 1, **self._startup}
            return self
        except Exception as error:
            self._fail(error.code if isinstance(error, WarmOCRError) else 'WARM_SETUP_FAILED')

    def _write(self, raw, completed, errors):
        try:
            offset = 0
            while offset < len(raw):
                count = self._process.stdin.write(raw[offset:])
                if count is None or count <= 0:
                    raise OSError('WRITE_FAILED')
                offset += count
            self._process.stdin.flush()
        except Exception:
            errors.append('WARM_WRITE_FAILED')
        finally:
            completed.set()

    def run(self, prepared, *, timeout_seconds=None):
        _need(self._lock.acquire(blocking=False), 'OCR_CONCURRENCY_LIMIT')
        try:
            _need(self._state == 'READY', 'WARM_SESSION_NOT_READY')
            _need(type(prepared) is bytes and 0 < len(prepared) <= MAX_IMAGE
                  and (prepared.startswith(b'\xff\xd8\xff') or prepared.startswith(b'\x89PNG\r\n\x1a\n')), 'WARM_IMAGE_LIMIT')
            self._state = 'BUSY'
            started = time.perf_counter()
            cold.check_resource_limits()
            request_id = str(uuid.uuid4())
            digest = hashlib.sha256(prepared).hexdigest()
            packet = _canonical({'type': 'ocr', 'request_id': request_id, 'image_sha256': digest,
                                 'image_base64': base64.b64encode(prepared).decode('ascii')}) + b'\n'
            _need(len(packet) <= MAX_REQUEST, 'WARM_REQUEST_LIMIT')
            budget = self._timeout_seconds if timeout_seconds is None else timeout_seconds
            _need(type(budget) in (int, float) and math.isfinite(budget) and 0 < budget <= self._timeout_seconds, 'WARM_READ_TIMEOUT')
            deadline = started + budget
            self._request_index += 1
            done, errors = threading.Event(), []
            self._writer = threading.Thread(target=self._write, args=(packet, done, errors), name='kk-warm-ocr-writer', daemon=True)
            self._writer.start()
            _need(done.wait(timeout=max(0, deadline - time.perf_counter())), 'WARM_DEADLINE')
            _need(not errors, 'WARM_WRITE_FAILED')
            self._writer.join()
            response, response_bytes = self._packet(deadline)
            _need(set(response) == {'type', 'protocol', 'request_id', 'image_sha256', 'ocr', 'decode_seconds', 'predict_seconds'}
                  and response['type'] == 'result' and response['request_id'] == request_id
                  and response['image_sha256'] == digest and type(response['ocr']) is dict
                  and _seconds(response['decode_seconds']) and _seconds(response['predict_seconds']), 'WARM_RESPONSE_BINDING')
            _need(len(_canonical(response['ocr'])) <= MAX_OCR_OUTPUT, 'WARM_OUTPUT_LIMIT')
            self.last_metadata = {'status': 'COMPLETED', 'model_initializations': 1,
                                  'request_index': self._request_index, 'request_seconds': time.perf_counter() - started,
                                  'decode_seconds': response['decode_seconds'], 'predict_seconds': response['predict_seconds'],
                                  'request_bytes': len(packet), 'response_bytes': response_bytes, **self._startup}
            self._state = 'READY'
            return response['ocr']
        except Exception as error:
            if self._state == 'BUSY':
                self._fail(error.code if isinstance(error, WarmOCRError) else 'WARM_REQUEST_FAILED')
            raise
        finally:
            self._lock.release()

    def _terminate(self):
        process = self._process
        if process is not None:
            if process.poll() is None:
                try:
                    if os.name == 'posix':
                        os.killpg(process.pid, signal.SIGTERM)
                    else:
                        process.terminate()
                except (ProcessLookupError, OSError):
                    process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    try:
                        if os.name == 'posix':
                            os.killpg(process.pid, signal.SIGKILL)
                        else:
                            process.kill()
                    except (ProcessLookupError, OSError):
                        process.kill()
                    process.wait(timeout=3)
            else:
                process.wait(timeout=1)
            for thread in (self._writer, self._reader):
                if thread is not None:
                    thread.join(timeout=2)
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    stream.close()
        self._process = None
        if self._temporary is not None:
            self._temporary.cleanup()
            self._temporary = None
        if self._owns_slot:
            cold._OCR_SEMAPHORE.release()
            self._owns_slot = False

    def _fail(self, code):
        self.last_metadata = {'status': 'FAILED', 'reason_code': code, 'request_index': self._request_index, **self._startup}
        try:
            self._terminate()
        finally:
            self._state = 'FAILED'
        raise WarmOCRError(code) from None

    def close(self):
        try:
            self._terminate()
        finally:
            if self._state != 'FAILED':
                self._state = 'CLOSED'
