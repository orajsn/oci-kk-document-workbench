"""Private JSONL worker: one fixed Paddle model initialization, bounded inputs.

Only main() imports native OCR libraries. Pure protocol helpers are testable
without Paddle, a network namespace, or private documents.
"""
import base64
import binascii
import hashlib
import json
import math
import os
import re
import socket
import sys
import time
import uuid

VERSION = 'warm-ocr-1.0'
MAX_IMAGE = 5 * 1024 * 1024
MAX_OCR_OUTPUT = 2 * 1024 * 1024
MAX_REQUEST = ((MAX_IMAGE + 2) // 3) * 4 + 4096
MAX_PACKET = MAX_OCR_OUTPUT + 16384
MAX_REQUESTS = 10000
DET = '/opt/kk-pilot/ocr-runtime-v1/models/PP-OCRv5_mobile_det_infer'
REC = '/opt/kk-pilot/ocr-runtime-v1/models/PP-OCRv5_server_rec_infer'


def canonical(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'), sort_keys=True).encode('utf-8')


def parse_packet(raw):
    if type(raw) is not bytes or not raw.endswith(b'\n') or len(raw) > MAX_REQUEST:
        raise ValueError('REQUEST_LIMIT')
    def unique(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('DUPLICATE_KEY')
            result[key] = value
        return result
    try:
        value = json.loads(raw, object_pairs_hook=unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError('NONFINITE')))
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError('REQUEST_JSON') from None
    if type(value) is not dict:
        raise ValueError('REQUEST_SHAPE')
    return value


def validate_request(value, seen):
    if (type(value) is not dict or set(value) != {'type', 'request_id', 'image_sha256', 'image_base64'}
            or value.get('type') != 'ocr' or len(seen) >= MAX_REQUESTS):
        raise ValueError('REQUEST_SHAPE')
    request_id = value.get('request_id')
    try:
        valid_id = type(request_id) is str and str(uuid.UUID(request_id)) == request_id
    except (ValueError, AttributeError, TypeError):
        valid_id = False
    if not valid_id or request_id in seen:
        raise ValueError('REQUEST_ID')
    digest, encoded = value.get('image_sha256'), value.get('image_base64')
    if type(digest) is not str or re.fullmatch('[0-9a-f]{64}', digest) is None:
        raise ValueError('IMAGE_HASH')
    if type(encoded) is not str or len(encoded) > ((MAX_IMAGE + 2) // 3) * 4:
        raise ValueError('IMAGE_LIMIT')
    try:
        image = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError('IMAGE_ENCODING') from None
    if (not 0 < len(image) <= MAX_IMAGE or hashlib.sha256(image).hexdigest() != digest
            or not (image.startswith(b'\xff\xd8\xff') or image.startswith(b'\x89PNG\r\n\x1a\n'))):
        raise ValueError('IMAGE_BINDING')
    seen.add(request_id)
    return request_id, digest, image


def select_observations(result):
    return {key: result[key] for key in ('rec_texts', 'rec_scores', 'rec_polys', 'rec_boxes',
            'text_word', 'text_word_region', 'text_word_boxes') if key in result}


def model_options(det, rec):
    if det != DET or rec != REC:
        raise ValueError('MODEL_PATH')
    # Deliberately identical to the installed one-document Paddle worker.
    return dict(text_detection_model_name='PP-OCRv5_mobile_det', text_detection_model_dir=det,
        text_recognition_model_name='PP-OCRv5_server_rec', text_recognition_model_dir=rec,
        device='cpu', cpu_threads=2, text_recognition_batch_size=1, use_doc_orientation_classify=False,
        use_doc_unwarping=False, use_textline_orientation=False, enable_hpi=False,
        return_word_box=True, text_rec_score_thresh=0)


def response_packet(request_id, digest, result, decode_seconds, predict_seconds):
    if any(type(t) not in (int, float) or not math.isfinite(t) or not 0 <= t <= 86400 for t in (decode_seconds, predict_seconds)):
        raise ValueError('TIMING_INVALID')
    if type(result) is not dict or len(canonical(result)) > MAX_OCR_OUTPUT:
        raise ValueError('OUTPUT_LIMIT')
    value = {'type': 'result', 'protocol': VERSION, 'request_id': request_id,
             'image_sha256': digest, 'ocr': result,
             'decode_seconds': decode_seconds, 'predict_seconds': predict_seconds}
    raw = canonical(value) + b'\n'
    if len(raw) > MAX_PACKET:
        raise ValueError('PACKET_LIMIT')
    return raw


def main():
    # Native-library output never shares the private protocol stream.
    protocol_fd = os.dup(sys.stdout.fileno())
    with os.fdopen(protocol_fd, 'wb', buffering=0) as output:
        with open(os.devnull, 'wb') as discard:
            os.dup2(discard.fileno(), sys.stdout.fileno())
            os.dup2(discard.fileno(), sys.stderr.fileno())
        def send(value):
            output.write(canonical(value) + b'\n')
        try:
            if (len(sys.argv) != 4 or os.name != 'posix'
                    or os.readlink('/proc/self/ns/net') == sys.argv[3]):
                raise ValueError('NETWORK_NAMESPACE_REQUIRED')
            options = model_options(sys.argv[1], sys.argv[2])
            def deny(*args, **kwargs):
                raise RuntimeError('NETWORK_DISABLED')
            socket.socket.connect = deny
            socket.socket.connect_ex = deny
            socket.create_connection = deny
            imported = time.perf_counter()
            import cv2
            import numpy as np
            from paddleocr import PaddleOCR
            import_seconds = time.perf_counter() - imported
            initialized = time.perf_counter()
            engine = PaddleOCR(**options)
            init_seconds = time.perf_counter() - initialized
            send({'type': 'ready', 'protocol': VERSION, 'pid': os.getpid(),
                  'library_import_seconds': import_seconds, 'model_initialization_seconds': init_seconds})
            seen = set()
            while True:
                raw = sys.stdin.buffer.readline(MAX_REQUEST + 1)
                if not raw:
                    return 0
                request_id, digest, image_bytes = validate_request(parse_packet(raw), seen)
                decoded = time.perf_counter()
                image = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
                if image is None or len(image.shape) != 3 or image.shape[0] * image.shape[1] > 20_000_000:
                    raise ValueError('IMAGE_DECODE')
                decode_seconds = time.perf_counter() - decoded
                predicted = time.perf_counter()
                result = engine.predict(image, return_word_box=True, text_rec_score_thresh=0)[0].json
                predict_seconds = time.perf_counter() - predicted
                result = select_observations(result.get('res', result))
                def convert(value):
                    if isinstance(value, np.ndarray):
                        return value.tolist()
                    if isinstance(value, np.generic):
                        return value.item()
                    raise TypeError('NON_NUMERIC')
                # Resolve native arrays without changing the observation schema.
                resolved = json.loads(json.dumps(result, default=convert, ensure_ascii=False, allow_nan=False))
                output.write(response_packet(request_id, digest, resolved, decode_seconds, predict_seconds))
        except BaseException:
            # No request text, exception body, model output or identifiers in errors.
            try:
                send({'type': 'failed', 'protocol': VERSION, 'code': 'WORKER_FAILED'})
            except Exception:
                pass
            return 2


if __name__ == '__main__':
    raise SystemExit(main())
