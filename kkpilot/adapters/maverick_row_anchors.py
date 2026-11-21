"""Conservative untrusted NIK-like OCR anchors. No reference data or row inference."""
import math
import re


def _vertices(item):
    try:
        points = item['bounding_polygon']['normalized_vertices']
        if len(points) < 3: return None
        out = [(p['x'], p['y']) for p in points]
        if any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1 for p in out for v in p): return None
        return out
    except (KeyError, TypeError): return None


def _inside(point, polygon):
    x, y = point
    inside = False
    for i, (x1,y1) in enumerate(polygon):
        x2,y2 = polygon[(i+1) % len(polygon)]
        cross = (x-x1)*(y2-y1) - (y-y1)*(x2-x1)
        if abs(cross) < 1e-12 and min(x1,x2)-1e-12 <= x <= max(x1,x2)+1e-12 and min(y1,y2)-1e-12 <= y <= max(y1,y2)+1e-12: return True
        if (y1 > y) != (y2 > y) and x < (x2-x1)*(y-y1)/(y2-y1)+x1: inside = not inside
    return inside


def _bbox(vertices):
    return [min(p[0] for p in vertices), min(p[1] for p in vertices), max(p[0] for p in vertices), max(p[1] for p in vertices)]


def derive_anchors(raw):
    if not isinstance(raw, dict) or not isinstance(raw.get('pages'), list): raise ValueError('INVALID_OCR')
    anchors=[]; selected=[]
    for pi,page in enumerate(raw['pages']):
        if not isinstance(page,dict): raise ValueError('INVALID_OCR')
        words=page.get('words') or []; tables=page.get('tables') or []; lines=page.get('lines') or []
        if not all(isinstance(x,list) for x in (words,tables,lines)): raise ValueError('INVALID_OCR')
        candidates=[]
        for ti,table in enumerate(tables):
            polygon=_vertices(table)
            if not polygon: continue
            indices=[i for i,w in enumerate(words) if isinstance(w,dict) and (v:=_vertices(w)) and all(_inside(p,polygon) for p in v)]
            if any(isinstance(words[i].get('text'),str) and words[i]['text'].strip().upper() == 'NIK' for i in indices): candidates.append((ti,polygon,indices))
        if len(candidates) != 1: continue
        ti,polygon,indices=candidates[0]; selected.append({'page_index':pi,'table_index':ti})
        for wi in indices:
            word=words[wi]; token=word.get('text')
            if not isinstance(token,str) or not re.fullmatch(r'[0-9]{16}',token): continue
            line_indices=[li for li,line in enumerate(lines) if isinstance(line,dict) and isinstance(line.get('word_indexes'),list) and wi in line['word_indexes']]
            anchors.append({'token':token,'page_index':pi,'table_index':ti,'word_index':wi,'line_indices':line_indices,'line_bboxes':[_bbox(v) if (v:=_vertices(lines[li])) else None for li in line_indices],'word_bbox':_bbox(_vertices(word)),'table_bbox':_bbox(polygon)})
    return {'status':'SELECTED' if selected else 'NO_UNAMBIGUOUS_NIK_TABLE', 'selected_tables':selected,'anchors':anchors,'interpretation':'untrusted OCR hints; not row counts or verified values'}

