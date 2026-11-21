"""Isolated local OCR subprocess. Input and output live in a private temp directory."""
import json
import os
import socket
import sys


def select_observations(result):
    return {key: result[key] for key in ('rec_texts', 'rec_scores', 'rec_polys', 'rec_boxes',
            'text_word', 'text_word_region', 'text_word_boxes') if key in result}


def main():
    if len(sys.argv) != 6 or os.readlink('/proc/self/ns/net') == sys.argv[5]:
        raise RuntimeError('NETWORK_NAMESPACE_REQUIRED')
    def deny(*args, **kwargs): raise RuntimeError('NETWORK_DISABLED')
    socket.socket.connect = deny
    socket.create_connection = deny
    import cv2
    import numpy as np
    from paddleocr import PaddleOCR
    raw = open(sys.argv[1], 'rb').read(5 * 1024 * 1024 + 1)
    if not raw or len(raw) >= 5 * 1024 * 1024: raise ValueError('INPUT_LIMIT')
    image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if image is None: raise ValueError('IMAGE_INVALID')
    engine = PaddleOCR(text_detection_model_name='PP-OCRv5_mobile_det', text_detection_model_dir=sys.argv[3],
        text_recognition_model_name='PP-OCRv5_server_rec', text_recognition_model_dir=sys.argv[4],
        device='cpu', cpu_threads=2, text_recognition_batch_size=1, use_doc_orientation_classify=False,
        use_doc_unwarping=False, use_textline_orientation=False, enable_hpi=False,
        return_word_box=True, text_rec_score_thresh=0)
    result = engine.predict(image, return_word_box=True, text_rec_score_thresh=0)[0].json
    result = result.get('res', result)
    output = select_observations(result)
    def convert(value):
        if isinstance(value, np.ndarray): return value.tolist()
        if isinstance(value, np.generic): return value.item()
        raise TypeError('NON_NUMERIC')
    data = json.dumps(output, default=convert, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
    if len(data) > 2 * 1024 * 1024: raise ValueError('OUTPUT_LIMIT')
    fd = os.open(sys.argv[2], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data); stream.flush(); os.fsync(stream.fileno())


if __name__ == '__main__': main()
