"""Reproduce approved, pixel-bound native source views for the pilot only."""
import hashlib
import io
import json
import re
from pathlib import Path
from PIL import Image, ImageOps
from . import gemini_focused_contract as contract


def prepare(source, source_hash, policy, manifest_path, manifest_hash):
    raw = Path(manifest_path).read_bytes()
    if len(raw) > 1024 * 1024 or hashlib.sha256(raw).hexdigest() != manifest_hash:
        raise ValueError('manifest')
    manifest = json.loads(raw)
    if (type(manifest) is not dict or set(manifest) != {'version', 'fields', 'records', 'reference_values_included'}
            or manifest['version'] != 'kk-focused-source-geometry-1.0'
            or manifest['fields'] != ['full_name', 'birth_place'] or manifest['reference_values_included'] is not False):
        raise ValueError('manifest shape')
    records = manifest['records']
    if not isinstance(records, list) or not 1 <= len(records) <= 12:
        raise ValueError('records')
    for item in records:
        if (type(item) is not dict or set(item) != {'source_sha256', 'approved_policy', 'canonical_size',
                'canonical_pixel_sha256', 'views', 'review_record_sha256', 'source_coverage_reviewed'}
                or item['source_coverage_reviewed'] is not True
                or any(type(item[k]) is not str or not re.fullmatch(r'[0-9a-f]{64}', item[k])
                       for k in ('source_sha256', 'canonical_pixel_sha256', 'review_record_sha256'))):
            raise ValueError('record shape')
        if type(item['views']) is not list or len(item['views']) != 2 or any(
                type(v) is not dict or set(v) != {'name', 'pixel_rect', 'size', 'pixel_sha256'}
                or type(v['pixel_sha256']) is not str or not re.fullmatch(r'[0-9a-f]{64}', v['pixel_sha256'])
                for v in item['views']):
            raise ValueError('view shape')
    if len({r['source_sha256'] for r in records}) != len(records):
        raise ValueError('duplicate source')
    matches = [r for r in records if r['source_sha256'] == source_hash]
    if len(matches) != 1 or hashlib.sha256(source).hexdigest() != source_hash:
        raise ValueError('source')
    record = matches[0]
    normalized_policy = {'crop': policy.get('crop'), 'rotate': policy.get('rotate', 0)}
    if record['approved_policy'] != normalized_policy:
        raise ValueError('policy')
    with Image.open(io.BytesIO(source)) as original:
        image = ImageOps.exif_transpose(original).convert('RGB')
    crop = normalized_policy['crop']
    if crop is not None:
        if not (0 <= crop[0] < crop[2] <= image.width and 0 <= crop[1] < crop[3] <= image.height):
            raise ValueError('crop')
        image = image.crop(crop)
    rotate = normalized_policy['rotate']
    if rotate:
        image = image.transpose({90: Image.Transpose.ROTATE_90, 180: Image.Transpose.ROTATE_180, 270: Image.Transpose.ROTATE_270}[rotate])
    if list(image.size) != record['canonical_size'] or hashlib.sha256(image.tobytes()).hexdigest() != record['canonical_pixel_sha256']:
        raise ValueError('canonical')
    views = []
    if len(record['views']) != 2:
        raise ValueError('views')
    for item, role in zip(record['views'], contract.ROLES):
        rect = item['pixel_rect']
        if item['name'] != role or len(rect) != 4 or any(type(v) is not int for v in rect) or not (0 <= rect[0] < rect[2] <= image.width and 0 <= rect[1] < rect[3] <= image.height):
            raise ValueError('rectangle')
        view = image.crop(rect)
        if list(view.size) != item['size'] or hashlib.sha256(view.tobytes()).hexdigest() != item['pixel_sha256']:
            raise ValueError('pixels')
        clean = Image.frombytes('RGB', view.size, view.tobytes())
        buf = io.BytesIO()
        clean.save(buf, format='PNG', compress_level=6, optimize=False)
        data = buf.getvalue()
        if len(data) >= 7_000_000:
            raise ValueError('size')
        views.append({'name': role, 'mime': 'image/png', 'bytes': data, 'sha256': hashlib.sha256(data).hexdigest(), 'pixel_rect': rect, 'size': item['size'], 'resampled': False, 'enhanced': False, 'source_image_sha256': source_hash})
    contract.validate_views(views)
    return views
