"""Bounded private local OCR invocation and source geometry binding."""
import hashlib
import io
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
from PIL import Image
from . import focused_source_views
from .document_preprocess import prepare_document_views

ROOT = Path('/opt/kk-pilot/ocr-runtime-v1')
PYTHON = ROOT / 'venv/bin/python'
NATIVE = ROOT / 'native/usr/lib64'
DET = ROOT / 'models/PP-OCRv5_mobile_det_infer'
REC = ROOT / 'models/PP-OCRv5_server_rec_infer'
MAX_OUTPUT = 2 * 1024 * 1024
_OCR_SEMAPHORE = threading.BoundedSemaphore(1)


def _sha(raw): return hashlib.sha256(raw).hexdigest()


def check_resource_limits(cgroup_file=Path('/proc/self/cgroup'), root=Path('/sys/fs/cgroup')):
    """Check effective cgroup v2 bounds across the process's ancestor hierarchy."""
    entries = cgroup_file.read_text().splitlines()
    paths = [line[3:] for line in entries if line.startswith('0::')]
    if len(paths) != 1 or not paths[0].startswith('/') or '..' in Path(paths[0]).parts:
        raise ValueError('OCR_CGROUP_INVALID')
    base = root.resolve(); current = (base / paths[0].lstrip('/')).resolve()
    if not current.is_relative_to(base): raise ValueError('OCR_CGROUP_INVALID')
    memory, swap, pids, cpu = [], [], [], []
    while True:
        def finite_value(name, target):
            value = (current / name).read_text().strip()
            if value == 'max': return
            if not value.isascii() or not value.isdecimal(): raise ValueError('OCR_CGROUP_INVALID')
            target.append(int(value))
        finite_value('memory.max', memory)
        finite_value('memory.swap.max', swap)
        finite_value('pids.max', pids)
        parts = (current / 'cpu.max').read_text().strip().split()
        if len(parts) != 2 or not parts[1].isascii() or not parts[1].isdecimal() or int(parts[1]) <= 0:
            raise ValueError('OCR_CGROUP_INVALID')
        if parts[0] != 'max':
            if not parts[0].isascii() or not parts[0].isdecimal() or int(parts[0]) <= 0:
                raise ValueError('OCR_CGROUP_INVALID')
            cpu.append((int(parts[0]), int(parts[1])))
        if (memory and min(memory) <= 4 * 1024**3 and swap and min(swap) == 0
                and pids and min(pids) <= 128 and cpu and any(quota <= 2 * period for quota, period in cpu)):
            return
        if current == base: break
        current = current.parent
    if not memory or min(memory) > 4 * 1024**3 or not swap or min(swap) != 0 \
            or not pids or min(pids) > 128 or not cpu or not any(quota <= 2 * period for quota, period in cpu):
        raise ValueError('OCR_CGROUP_UNBOUNDED')


def verify_models(path, digest):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(ROOT.resolve()): raise ValueError('OCR_MANIFEST_PATH')
    with path.open('rb') as stream: raw = stream.read(65537)
    if len(raw) > 65536 or _sha(raw) != digest: raise ValueError('OCR_MANIFEST_PIN')
    manifest = json.loads(raw)
    expected = {f'{model}/{name}' for model in (DET.name, REC.name)
                for name in ('inference.json', 'inference.pdiparams', 'inference.yml')}
    if type(manifest) is not dict or set(manifest) != {'version', 'files'} or manifest['version'] != 'paddle-server-models-1.0':
        raise ValueError('OCR_MANIFEST_SHAPE')
    files = manifest['files']
    if type(files) is not dict or set(files) != expected: raise ValueError('OCR_MANIFEST_FILES')
    for relative, pin in files.items():
        file = ROOT / 'models' / relative
        if (type(pin) is not str or len(pin) != 64 or file.is_symlink() or file.parent.is_symlink()
                or (ROOT / 'models').is_symlink() or not file.is_file()
                or not file.resolve().is_relative_to(ROOT.resolve())): raise ValueError('OCR_MODEL_PATH')
        digest_obj = hashlib.sha256()
        with file.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''): digest_obj.update(chunk)
        if digest_obj.hexdigest() != pin: raise ValueError('OCR_MODEL_PIN')


def geometry(source, source_hash, prepared, policy, manifest_path, manifest_hash):
    # The existing preparation validates canonical transform and both source pixel hashes.
    focused_source_views.prepare(source, source_hash, policy, manifest_path, manifest_hash)
    raw = Path(manifest_path).read_bytes()
    if _sha(raw) != manifest_hash: raise ValueError('GEOMETRY_PIN')
    matches = [record for record in json.loads(raw)['records'] if record['source_sha256'] == source_hash]
    if len(matches) != 1: raise ValueError('GEOMETRY_BINDING')
    record = matches[0]; width, height = record['canonical_size']
    _, meta = prepare_document_views(source, {'enabled': True, 'auto_crop': True, 'table_views': False,
        'max_edge': 4096, 'crop': policy.get('crop'), 'rotate': policy.get('rotate', 0)})
    if any(item.get('type') == 'perspective' for item in meta.get('transforms', [])):
        return {'mapping': 'UNAVAILABLE_PERSPECTIVE'}
    with Image.open(io.BytesIO(prepared)) as image: pw, ph = image.size
    scale = min(1.0, 4096 / max(width, height))
    if abs(pw-width*scale) > 1.1 or abs(ph-height*scale) > 1.1: raise ValueError('GEOMETRY_SCALE')
    rects = {view['name']: view['pixel_rect'] for view in record['views']}
    return {'mapping': 'RESIZE_ONLY', 'scale': [width/pw, height/ph],
            'name_rect': rects['name_table'], 'identifier_rect': rects['middle_member_table']}


def run(prepared, config, capture=None):
    if not _OCR_SEMAPHORE.acquire(blocking=False): raise ValueError('OCR_CONCURRENCY_LIMIT')
    try:
        check_resource_limits()
        return _run(prepared, config, capture)
    finally:
        _OCR_SEMAPHORE.release()


def _run(prepared, config, capture=None):
    verify_models(config['model_manifest_path'], config['model_manifest_sha256'])
    if os.name != 'posix': raise ValueError('OCR_PLATFORM')
    parent_ns = os.readlink('/proc/self/ns/net')
    worker = Path(__file__).with_name('paddle_ocr_worker.py').resolve()
    with tempfile.TemporaryDirectory(prefix='kk-ocr-') as temp:
        directory = Path(temp)
        image_path, output_path = directory / 'input.jpg', directory / 'output.json'
        fd = os.open(image_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream: stream.write(prepared)
        env = {'PATH': '/usr/bin:/bin', 'TMPDIR': temp, 'LD_LIBRARY_PATH': str(NATIVE),
               'PADDLE_PDX_CACHE_HOME': temp, 'PADDLE_HOME': temp, 'XDG_CACHE_HOME': temp, 'HF_HOME': temp,
               'PYTHONNOUSERSITE': '1', 'PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK': 'True',
               'DISABLE_MODEL_SOURCE_CHECK': 'True', 'OMP_NUM_THREADS': '2',
               'OPENBLAS_NUM_THREADS': '2', 'MKL_NUM_THREADS': '2', 'GLOG_minloglevel': '3'}
        command = ['/usr/bin/unshare', '--user', '--map-root-user', '--net', str(PYTHON), '-I', '-B',
                   str(worker), str(image_path), str(output_path), str(DET), str(REC), parent_ns]
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, env=env, start_new_session=True)
        try:
            code = process.wait(timeout=config['timeout_seconds'])
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL); process.wait()
            raise TimeoutError('OCR_TIMEOUT') from None
        if code != 0 or output_path.is_symlink() or not output_path.is_file(): raise ValueError('OCR_CHILD_FAILED')
        with output_path.open('rb') as stream: raw = stream.read(MAX_OUTPUT + 1)
        if len(raw) > MAX_OUTPUT: raise ValueError('OCR_OUTPUT_LIMIT')
        result = json.loads(raw)
        if type(result) is not dict: raise ValueError('OCR_OUTPUT_INVALID')
        if capture is not None: capture(result)
        return result
