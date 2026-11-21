"""Recreate only the source-reviewed population NIK views for the fixed pilot."""
import hashlib
import io
import json
import re
from pathlib import Path
from PIL import Image, ImageOps

ROLES = ('row_identity_column', 'nik_column')
class NikSourceViewsHeld(ValueError):
    """The original, geometry, recipe, or historical bytes did not match."""

def _need(test, reason):
    if not test: raise NikSourceViewsHeld(reason)
def _sha(value): return hashlib.sha256(value).hexdigest()
def _hash(value): return type(value) is str and re.fullmatch(r'[0-9a-f]{64}', value) is not None

def prepare(source, source_sha256, policy, manifest_path, manifest_sha256):
    try:
        _need(type(source) is bytes and 0 < len(source) <= 10 * 1024 * 1024, 'SOURCE_SIZE')
        _need(_hash(source_sha256) and _sha(source) == source_sha256, 'SOURCE_HASH')
        _need(_hash(manifest_sha256), 'MANIFEST_PIN')
        raw = Path(manifest_path).read_bytes()
        _need(len(raw) <= 1024 * 1024 and _sha(raw) == manifest_sha256, 'MANIFEST_HASH')
        doc = json.loads(raw)
        _need(type(doc) is dict and set(doc) == {'version','reference_values_included','records'} and doc['version'] == 'kk-population-nik-source-geometry-1.0' and doc['reference_values_included'] is False, 'MANIFEST_SHAPE')
        records = doc['records']
        _need(type(records) is list and len(records) == 12 and all(type(r) is dict and _hash(r.get('source_sha256')) for r in records) and len({r['source_sha256'] for r in records}) == 12, 'RECORDS')
        matching = [r for r in records if r['source_sha256'] == source_sha256]
        _need(len(matching) == 1, 'SOURCE_NOT_REVIEWED')
        r = matching[0]
        _need(set(r) == {'source_sha256','approved_policy','canonical_size','canonical_pixel_sha256','historical_canonical_png_sha256','views_metadata_sha256','location_sha256','source_review_sha256','source_coverage_reviewed','views'} and r['source_coverage_reviewed'] is True and all(_hash(r[k]) for k in ('canonical_pixel_sha256','historical_canonical_png_sha256','views_metadata_sha256','location_sha256','source_review_sha256')), 'RECORD_SHAPE')
        _need(type(policy) is dict and set(policy) <= {'crop','rotate'} and policy.get('crop') == r['approved_policy']['crop'] and policy.get('rotate', 0) == r['approved_policy']['rotate'], 'POLICY')
        crop, rotate = r['approved_policy']['crop'], r['approved_policy']['rotate']
        _need(type(rotate) is int and rotate in (0,90,180,270), 'ROTATION')
        with Image.open(io.BytesIO(source)) as im:
            _need(im.format in ('JPEG','PNG') and im.mode in ('RGB','L') and getattr(im,'n_frames',1)==1 and im.width*im.height <= 20_000_000, 'IMAGE_FORMAT')
            im.load(); image = ImageOps.exif_transpose(im).convert('RGB')
        if crop is not None:
            _need(type(crop) is list and len(crop)==4 and all(type(x) is int for x in crop) and 0 <= crop[0] < crop[2] <= image.width and 0 <= crop[1] < crop[3] <= image.height, 'CROP')
            image=image.crop(crop)
        if rotate: image=image.transpose({90:Image.Transpose.ROTATE_90,180:Image.Transpose.ROTATE_180,270:Image.Transpose.ROTATE_270}[rotate])
        image=Image.frombytes('RGB',image.size,image.tobytes())
        _need(list(image.size)==r['canonical_size'] and _sha(image.tobytes())==r['canonical_pixel_sha256'], 'CANONICAL_PIXELS')
        buf=io.BytesIO(); image.save(buf,format='PNG',compress_level=6,optimize=False)
        _need(_sha(buf.getvalue())==r['historical_canonical_png_sha256'], 'CANONICAL_ENCODING')
        _need(type(r['views']) is list and len(r['views'])==2, 'VIEWS')
        views=[]
        for v, role in zip(r['views'], ROLES):
            _need(type(v) is dict and set(v)=={'name','pixel_rect','size','pixel_sha256','historical_png_sha256'} and v['name']==role and _hash(v['pixel_sha256']) and _hash(v['historical_png_sha256']), 'VIEW_SHAPE')
            rect=v['pixel_rect']
            _need(type(rect) is list and len(rect)==4 and all(type(x) is int for x in rect) and 0 <= rect[0] < rect[2] <= image.width and 0 <= rect[1] < rect[3] <= image.height, 'VIEW_RECT')
            im=image.crop(rect); _need(list(im.size)==v['size'] and _sha(im.tobytes())==v['pixel_sha256'], 'VIEW_PIXELS')
            buf=io.BytesIO(); im.save(buf, format='PNG', compress_level=6, optimize=False); data=buf.getvalue()
            _need(0 < len(data) <= 5*1024*1024 and _sha(data)==v['historical_png_sha256'], 'VIEW_ENCODING')
            views.append({'name':role,'mime':'image/png','bytes':data,'sha256':_sha(data),'pixel_sha256':v['pixel_sha256'],'pixel_rect':rect,'size':v['size'],'shared_y_extent':[rect[1],rect[3]],'source_image_sha256':source_sha256,'resampled':False,'enhanced':False})
        _need(views[0]['shared_y_extent']==views[1]['shared_y_extent'] and sum(len(v['bytes']) for v in views)<=8*1024*1024, 'VIEW_ALIGNMENT')
        return views, {'status':'PASS','manifest_sha256':manifest_sha256,'source_sha256':source_sha256,'canonical_sha256':r['historical_canonical_png_sha256'],'canonical_pixel_sha256':r['canonical_pixel_sha256'],'views_metadata_sha256':r['views_metadata_sha256'],'source_review_sha256':r['source_review_sha256'],'view_sha256':[v['sha256'] for v in views], 'resampled':False,'enhanced':False}
    except NikSourceViewsHeld: raise
    except Exception as exc: raise NikSourceViewsHeld('PREPARATION_FAILED') from exc
