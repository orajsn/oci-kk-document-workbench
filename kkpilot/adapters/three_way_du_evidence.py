"""Pure normalized DU-to-prepared-image evidence conversion; no value repair."""
import math


def _need(value):
    if not value:
        raise ValueError('DU_EVIDENCE_INVALID')


def _box(item, width, height):
    polygon = item.get('bounding_polygon')
    vertices = polygon.get('normalized_vertices') if type(polygon) is dict else None
    if (type(vertices) is not list or len(vertices) < 3 or any(
            type(v) is not dict or any(type(v.get(k)) not in (int, float)
            or not math.isfinite(v[k]) or not 0 <= v[k] <= 1 for k in ('x', 'y')) for v in vertices)):
        return None
    xs, ys = [v['x'] * width for v in vertices], [v['y'] * height for v in vertices]
    b = [min(xs), min(ys), max(xs), max(ys)]
    return b if b[0] < b[2] and b[1] < b[3] else None


def convert(raw, width, height):
    """Require a single page in precisely the submitted prepared-image frame.

    Missing polygons stay unlocated so the policy can detect ambiguity. A malformed
    shape or wrong page frame holds the whole stage, rather than dropping evidence.
    """
    _need(type(width) is int and type(height) is int and width > 0 and height > 0)
    _need(type(raw) is dict and type(raw.get('pages')) is list and len(raw['pages']) == 1)
    page = raw['pages'][0]; _need(type(page) is dict)
    dims = page.get('dimensions'); _need(type(dims) is dict)
    _need(dims.get('unit') == 'PIXEL' and dims.get('width') == width and dims.get('height') == height)
    def items(values):
        _need(type(values) is list and len(values) <= 20000)
        output = []
        for item in values:
            _need(type(item) is dict and (item.get('text') is None or type(item.get('text')) is str))
            confidence = item.get('confidence')
            _need(confidence is None or type(confidence) in (int, float) and math.isfinite(confidence) and 0 <= confidence <= 1)
            output.append({'text': item.get('text'), 'confidence': confidence, 'bbox_pixels': _box(item, width, height)})
        return output
    lines = items(page.get('lines')); cells = []
    tables = page.get('tables', []); _need(type(tables) is list and len(tables) <= 1000)
    for table in tables:
        _need(type(table) is dict)
        for kind in ('header_rows', 'body_rows', 'footer_rows'):
            rows = table.get(kind) or []; _need(type(rows) is list)
            for row in rows:
                _need(type(row) is dict)
                cells.extend(items(row.get('cells')))
                _need(len(cells) <= 20000)
    return lines, cells
