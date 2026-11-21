"""Optional document image preparation; no text reconstruction or source mutation."""
from hashlib import sha256
from io import BytesIO

from .errors import AdapterError
from ..validation import inspect_image


def prepare_document_views(source_bytes, policy=None):
    """Return views and PII-free metadata; feature is disabled by default.

    Crop is in EXIF-oriented coordinates; rotation follows crop. Manual crop
    suppresses auto-crop. Uncertain boundaries always retain full context.
    """
    if policy is None:
        policy = {}
    allowed = {'enabled', 'auto_crop', 'table_views', 'crop', 'rotate', 'max_edge'}
    if not isinstance(policy, dict) or set(policy) - allowed:
        raise AdapterError('POLICY_INVALID')
    for key in ('enabled', 'auto_crop', 'table_views'):
        if key in policy and type(policy[key]) is not bool:
            raise AdapterError('POLICY_INVALID')
    if 'crop' in policy and policy['crop'] is not None and (
            not isinstance(policy['crop'], (list, tuple)) or len(policy['crop']) != 4 or
            any(type(x) is not int for x in policy['crop'])):
        raise AdapterError('CROP_INVALID')
    if type(policy.get('rotate', 0)) is not int or policy.get('rotate', 0) not in (0, 90, 180, 270):
        raise AdapterError('ROTATION_INVALID')
    if type(policy.get('max_edge', 2400)) is not int or not 256 <= policy.get('max_edge', 2400) <= 4096:
        raise AdapterError('IMAGE_EDGE_LIMIT_INVALID')
    if not isinstance(source_bytes, bytes) or len(source_bytes) > 10_000_000:
        raise AdapterError('IMAGE_TOO_LARGE')
    mime, _, width, height = inspect_image(source_bytes, max_pixels=20_000_000)
    digest = sha256(source_bytes).hexdigest()
    original = {'name': 'original', 'bytes': source_bytes, 'mime': mime,
                'sha256': digest, 'size': [width, height]}
    meta = {'original_sha256': digest, 'original_size': [width, height],
            'status': 'DISABLED', 'transforms': [], 'version': '1.0'}
    if policy.get('enabled') is not True:
        return [original], meta
    try:
        from PIL import Image, ImageOps, ImageFilter, ImageStat
    except ImportError:
        meta.update(status='UNAVAILABLE', reason='PILLOW_MISSING')
        return ([] if policy.get('crop') is not None else [original]), meta
    try:
        with Image.open(BytesIO(source_bytes)) as src:
            orientation = src.getexif().get(274, 1)
            picture = ImageOps.exif_transpose(src).convert('RGB')
    except Exception as exc:
        raise AdapterError('IMAGE_DECODE_FAILED') from exc
    meta['exif_orientation'] = orientation
    meta['transforms'].append({'type': 'exif_transpose', 'orientation': orientation,
                               'size': list(picture.size)})
    crop = policy.get('crop')
    if crop is not None:
        if (not isinstance(crop, (tuple, list)) or len(crop) != 4 or
                any(type(x) is not int for x in crop)):
            raise AdapterError('CROP_INVALID')
        l, t, r, b = crop
        if not (0 <= l < r <= picture.width and 0 <= t < b <= picture.height):
            raise AdapterError('CROP_OUT_OF_BOUNDS')
        picture = picture.crop(tuple(crop))
        meta['transforms'].append({'type': 'manual_crop', 'bounds': list(crop)})
    rotate = policy.get('rotate', 0)
    if type(rotate) is not int or rotate not in (0, 90, 180, 270):
        raise AdapterError('ROTATION_INVALID')
    if rotate:
        picture = picture.rotate(rotate, expand=True)
        meta['transforms'].append({'type': 'manual_rotation', 'degrees_ccw': rotate})
    # Context is restricted to the operator-approved region and orientation.
    context = picture.copy()
    gray = picture.convert('L')
    meta['quality'] = {'width': picture.width, 'height': picture.height,
                       'grayscale_stddev': round(ImageStat.Stat(gray).stddev[0], 2),
                       'edge_stddev': round(ImageStat.Stat(gray.filter(ImageFilter.FIND_EDGES)).stddev[0], 2)}
    status, reason = 'READY', None
    if policy.get('auto_crop') and crop is None:
        try:
            import cv2
            import numpy as np
        except ImportError:
            status, reason = 'UNAVAILABLE', 'OPENCV_NUMPY_MISSING'
        else:
            arr = np.asarray(picture)
            scale = min(1.0, 1600 / max(picture.size))
            small = cv2.resize(arr, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else arr
            g = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
            edge = cv2.Canny(g, 50, 150)
            # Bridge tiny rasterization gaps only in the detection mask; source
            # pixels used for the perspective view remain untouched.
            edge = cv2.morphologyEx(edge, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
            contours, _ = cv2.findContours(edge, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            h, w = g.shape
            quads = []
            for contour in contours:
                area = cv2.contourArea(contour)
                # Keep smaller candidates to catch a second substantial panel.
                if area < w * h * .05:
                    continue
                poly = cv2.approxPolyDP(contour, .02 * cv2.arcLength(contour, True), True)
                if len(poly) == 4 and cv2.isContourConvex(poly):
                    quads.append((area, poly.reshape(4, 2).astype('float32')))
            quads.sort(key=lambda q: q[0], reverse=True)
            if not quads or quads[0][0] < w * h * .12:
                status, reason = 'NEEDS_REVIEW', 'NO_CONFIDENT_BOUNDARY'
            elif len(quads) > 1 and quads[1][0] >= quads[0][0] * .3:
                status, reason = 'NEEDS_REVIEW', 'MULTIPLE_LARGE_BOUNDARIES'
            else:
                pts = quads[0][1]
                if (pts[:, 0].min() < w*.02 or pts[:, 0].max() > w*.98 or
                        pts[:, 1].min() < h*.02 or pts[:, 1].max() > h*.98):
                    status, reason = 'NEEDS_REVIEW', 'BOUNDARY_NEAR_IMAGE_EDGE'
                else:
                    s, d = pts.sum(axis=1), np.diff(pts, axis=1).reshape(-1)
                    ordered = np.array([pts[s.argmin()], pts[d.argmin()], pts[s.argmax()], pts[d.argmax()]], dtype='float32') / scale
                    if len({tuple(p) for p in ordered}) != 4:
                        status, reason = 'NEEDS_REVIEW', 'DEGENERATE_BOUNDARY'
                    else:
                        tl, tr, br, bl = ordered
                        ow = int(max(np.linalg.norm(br-bl), np.linalg.norm(tr-tl)))
                        oh = int(max(np.linalg.norm(tr-br), np.linalg.norm(tl-bl)))
                        expanded = ordered.mean(axis=0) + (ordered-ordered.mean(axis=0))*1.025
                        if (min(ow, oh) < 256 or max(ow, oh)/min(ow, oh) > 4 or
                                expanded[:, 0].min() < 0 or expanded[:, 0].max() >= picture.width or
                                expanded[:, 1].min() < 0 or expanded[:, 1].max() >= picture.height):
                            status, reason = 'NEEDS_REVIEW', 'IMPLAUSIBLE_GEOMETRY_OR_MARGIN'
                        else:
                            ow, oh = int(ow*1.025), int(oh*1.025)
                            dst = np.float32([[0,0],[ow-1,0],[ow-1,oh-1],[0,oh-1]])
                            matrix = cv2.getPerspectiveTransform(expanded.astype('float32'), dst)
                            picture = Image.fromarray(cv2.warpPerspective(arr, matrix, (ow,oh)))
                            meta['transforms'].append({'type': 'perspective', 'corners': expanded.round(2).tolist(), 'size': [ow,oh]})
    meta['status'] = status
    if reason:
        meta['reason'] = reason
        # A failed or unavailable auto-localization never changes the input.
        return [original], meta
    max_edge = policy.get('max_edge', 2400)
    specs = [('context', context)]
    if status == 'READY':
        if any(t['type'] == 'perspective' for t in meta['transforms']):
            specs.append(('document', picture))
        if policy.get('table_views', True):
            split, overlap = picture.height // 2, max(1, picture.height // 10)
            specs += [('top', picture.crop((0, 0, picture.width, min(picture.height, split+overlap)))),
                      ('bottom', picture.crop((0, max(0, split-overlap), picture.width, picture.height)))]
    views = []
    for name, image in specs:
        image = image.copy()
        image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        out = BytesIO()
        image.save(out, format='JPEG', quality=92)
        data = out.getvalue()
        views.append({'name': name, 'bytes': data, 'mime': 'image/jpeg',
                      'size': list(image.size), 'sha256': sha256(data).hexdigest()})
    if sum(len(v['bytes']) for v in views) > 10_000_000:
        meta.update(status='NEEDS_REVIEW', reason='TOTAL_BYTES_LIMIT')
        return ([] if crop is not None else [original]), meta
    return views, meta
