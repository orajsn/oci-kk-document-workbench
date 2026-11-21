"""Review-required geometry mapping of cached Document Understanding output.

All values are OCR observations; geometry is only used for association.  Optional
panel metadata uses normalized original-image crop boxes: per page, ``role`` is
``context`` or ``table``, ``table_id`` groups sibling tiles, and ``crop`` is
[x0, y0, x1, y1].  It must be supplied by the caller, not inferred from values.
"""
from __future__ import annotations

import re
from collections import defaultdict
from .du_kk_parser import _box, _household, _norm, _prepare_table, HEADERS, DUParseError
from ..validation import HOUSEHOLD_FIELDS, MEMBER_FIELDS, assert_valid_payload


def _transform(box, crop):
    if box is None:
        return None
    x0, y0, x1, y1 = crop
    return (x0 + box[0] * (x1-x0), y0 + box[1] * (y1-y0),
            x0 + box[2] * (x1-x0), y0 + box[3] * (y1-y0))


def _center(box):
    return ((box[0]+box[2])/2, (box[1]+box[3])/2)


def _caption(text):
    return bool(re.fullmatch(r'\s*\(\s*\d{1,2}\s*\)\s*', text))


def _header_info(table, crop):
    """Use DU column ownership only for header identification, never body rows."""
    mapping, body = _prepare_table(table)
    headers = list(table.get('header_rows') or [])
    rows = table.get('body_rows') or []
    if not headers:
        headers = rows[:len(rows)-len(body)]
    if not headers:
        return {}, None
    by_col = defaultdict(list)
    for row in headers:
        for cell in row.get('cells') or []:
            col = cell.get('column_index')
            box = _transform(_box(cell), crop)
            if isinstance(col, int) and box:
                by_col[col].append((cell.get('text') or '', box))
    # Parent labels in a spanning cell are not a leaf value.
    for col, entries in by_col.items():
        text = _norm(' '.join(e[0] for e in entries))
        if text in HEADERS:
            mapping[col] = HEADERS[text]
        elif col not in mapping:
            hits = {HEADERS[_norm(t)] for t, _ in entries if _norm(t) in HEADERS}
            hits.discard('combined_parent_name') if hits & {'father_name', 'mother_name'} else None
            if len(hits) == 1:
                mapping[col] = hits.pop()
    # Header cell boxes can span rows, but their horizontal extent is usable only
    # when it is narrow and does not overlap the other mapped header centers.
    cols = []
    for col, field in mapping.items():
        if col not in by_col:
            continue
        boxes = [b for _, b in by_col[col]]
        left = min(b[0] for b in boxes); right = max(b[2] for b in boxes)
        cols.append((col, field, left, right, (left+right)/2))
    cols.sort(key=lambda c: c[4])
    if len(cols) < 2:
        return {}, None
    if any(cols[i][4] >= cols[i+1][4] - 1e-6 for i in range(len(cols)-1)):
        return {}, None
    bottom = max(b[3] for entries in by_col.values() for _, b in entries)
    return {c[0]: c for c in cols}, bottom



def _word_headers(page, table, crop):
    """Recognize only complete, exact public labels in the upper table band."""
    tb = _transform(_box(table), crop)
    if not tb:
        return {}, None
    roi_bottom = tb[1] + min(.04, (tb[3]-tb[1])*.25)
    words = []
    for w in page.get('words') or []:
        b = _transform(_box(w), crop)
        if b and tb[0] <= _center(b)[0] <= tb[2] and tb[1] <= _center(b)[1] <= roi_bottom:
            words.append(((w.get('text') or '').strip(), b))
    # Adjacent words on the same line may form an exact fixed alias. Single
    # generic words such as NO, NAMA and TANGGAL are not accepted alone.
    aliases = dict(HEADERS, NIKS='nik_raw', NIKNIKS='nik_raw')
    found = []
    for i, (text, b) in enumerate(words):
        candidates = [(text,b)]
        for j, (t2,b2) in enumerate(words):
            if i == j or b2[0] < b[0] or b2[0]-b[2] > .012:
                continue
            if abs(_center(b)[1]-_center(b2)[1]) <= .008:
                candidates.append((text+t2,(min(b[0],b2[0]),min(b[1],b2[1]),max(b[2],b2[2]),max(b[3],b2[3]))))
        for text2, bb in candidates:
            key = _norm(text2)
            if key in aliases and key not in ('NO','NOMOR','AYAH','IBU'):
                found.append((aliases[key],bb))
    unique = {}
    for field, b in found:
        if field in unique and abs(_center(unique[field])[0]-_center(b)[0]) > .01:
            unique[field] = None
        elif field not in unique:
            unique[field] = b
    cols = {}
    for field,b in unique.items():
        if b:
            k=len(cols); cols[k]=(k,field,b[0],b[2],_center(b)[0])
    if not cols:
        return {},None
    return cols, roi_bottom

def _number_words(page, crop, table_box, cols, header_bottom):
    # No must lie in the explicit No column or just left of the first mapped
    # data column. Never accept a number located in a document-number column.
    ordered = sorted(cols.values(), key=lambda c: c[4])
    no = [c for c in ordered if c[1] == 'row_number']
    first_data = next((c for c in ordered if c[1] != 'row_number'), None)
    if not first_data or len(no) > 1:
        return []
    if no:
        if no[0] != ordered[0]:
            return []
        right = (no[0][4]+first_data[4])/2
        left = min(table_box[0], no[0][2]) - .012
    else:
        left = max(0, table_box[0] - .08)
        right = first_data[2] - .002
    if right <= left:
        return []
    found = []
    for word in page.get('words') or []:
        text = (word.get('text') or '').strip()
        box = _transform(_box(word), crop)
        if not box or not re.fullmatch(r'[0-9]{1,3}', text):
            continue
        n = int(text); x, y = _center(box)
        if 1 <= n <= 100 and left <= x < right and header_bottom < y <= table_box[3]:
            found.append((n, y, box))
    return sorted(found, key=lambda a: a[1])


def _collect_words(page, crop, cols, anchors, table_box, header_bottom, strict_columns=False):
    """Associate words with the nearest No band; ties are left unresolved."""
    ordered = sorted(cols.values(), key=lambda c: c[4])
    words = defaultdict(lambda: defaultdict(list))
    for word in page.get('words') or []:
        text = (word.get('text') or '').strip()
        box = _transform(_box(word), crop)
        if not text or not box or _caption(text):
            continue
        x, y = _center(box)
        if not header_bottom < y <= table_box[3] or not table_box[0]-.01 <= x <= table_box[2]+.01:
            continue
        distances = sorted((abs(y-a[1]), a[0], a[1]) for a in anchors)
        if not distances or (len(distances)>1 and abs(distances[0][0]-distances[1][0]) < .001):
            continue
        # Avoid assigning captions or distant empty-template text to a member.
        nearest = distances[0]
        max_gap = min(.035, (min((abs(nearest[2]-a[1]) for a in anchors if a[1] != nearest[2]), default=.07))/2)
        if nearest[0] > max_gap:
            continue
        candidates = []
        for i, col in enumerate(ordered):
            if col[1] == 'row_number':
                continue
            left = (ordered[i-1][4]+col[4])/2 if i else col[2]-.01
            right = (col[4]+ordered[i+1][4])/2 if i+1 < len(ordered) else col[3]+.01
            if strict_columns:
                left, right = col[2]-.008, col[3]+.008
            if left <= x < right:
                candidates.append(col[1])
        if len(candidates) == 1:
            words[nearest[1]][candidates[0]].append((y, x, text))
    return words


def parse_du_geometry(raw, panel_metadata=None, extra_anchors=None):
    """Return canonical payload and redacted counts; never declares review complete.

    A panel group may borrow anchors only from the same caller-specified table_id
    in the same original coordinate frame. Duplicate row IDs in any one panel
    invalidate that panel's anchors. Competing field readings remain null.
    """
    if not isinstance(raw, dict) or not isinstance(raw.get('pages'), list):
        raise DUParseError('INVALID_DU_RESPONSE')
    pages = raw['pages']
    metadata = panel_metadata or [{} for _ in pages]
    if len(metadata) != len(pages):
        raise DUParseError('INVALID_PANEL_METADATA')
    values = defaultdict(list); panels = []; flags = []; context_tables = []
    for index, (page, meta) in enumerate(zip(pages, metadata)):
        crop = meta.get('crop', [0, 0, 1, 1])
        if len(crop) != 4 or not (0 <= crop[0] < crop[2] <= 1 and 0 <= crop[1] < crop[3] <= 1):
            raise DUParseError('INVALID_PANEL_METADATA')
        role = meta.get('role', 'both')
        if role not in ('both', 'context', 'table'):
            raise DUParseError('INVALID_PANEL_METADATA')
        if role in ('both', 'context'):
            first = min((b[1] for t in page.get('tables') or [] if (b := _box(t))), default=1.0)
            _household(page, first, values)
            for table in page.get('tables') or []:
                cbox = _transform(_box(table), crop)
                ccols, cbottom = _word_headers(page, table, crop)
                if cbox and ccols:
                    context_tables.append((cbox, ccols, cbottom))
        if role == 'context':
            continue
        for table_index, table in enumerate(page.get('tables') or []):
            box = _transform(_box(table), crop)
            cols, bottom = _header_info(table, crop)
            if not box:
                continue
            strict = False
            if not cols or bottom is None or bottom >= box[3]:
                # Reuse exact context-page header labels in original coordinates.
                matches = [c for c in context_tables if abs(c[0][1]-box[1]) < .04 and min(c[0][2],box[2]) > max(c[0][0],box[0])]
                if len(matches) != 1:
                    continue
                _, cols, bottom = matches[0]
                cols = {k:c for k,c in cols.items() if box[0]-.02 <= c[4] <= box[2]+.02}
                if not cols:
                    continue
                strict = True
            anchors = _number_words(page, crop, box, cols, bottom) if len(cols) >= 2 else []
            group = meta.get('table_id', f'page-{index}-table-{table_index}')
            supplied = (extra_anchors or {}).get(group, [])
            conflicting_sources = False
            if supplied:
                # Caller supplies observed OCR digits and original-normalized boxes.
                # Require a unique, valid token and geometry within this table band.
                candidate = []
                for item in supplied:
                    text = str(item.get('text','')).strip(); b = item.get('box')
                    if not re.fullmatch(r'[0-9]{1,3}', text) or not 1 <= int(text) <= 100 or not isinstance(b,(list,tuple)) or len(b)!=4:
                        raise DUParseError('INVALID_EXTRA_ANCHOR')
                    x,y = _center(b)
                    if not (0 <= b[0] <= x <= b[2] <= 1 and bottom < y <= box[3] and 0 <= b[1] <= b[3] <= 1):
                        raise DUParseError('INVALID_EXTRA_ANCHOR')
                    candidate.append((int(text),y,b))
                if anchors:
                    # Mixed OCR sources are not silently reconciled.
                    flags.append('AMBIGUOUS_PRINTED_ROW'); anchors = []; conflicting_sources = True
                else:
                    anchors = sorted(candidate,key=lambda a:a[1])
            duplicate = conflicting_sources or len({a[0] for a in anchors}) != len(anchors)
            if duplicate:
                anchors = []
                flags.append('AMBIGUOUS_PRINTED_ROW')
            panels.append({'page': page, 'crop': crop, 'box': box, 'cols': cols,
                           'bottom': bottom, 'anchors': anchors, 'group': group, 'duplicate': duplicate, 'strict': strict})
    rows = defaultdict(lambda: defaultdict(list)); parents = set()
    # A caller-defined tile group refers to the same physical table. Matching
    # row IDs must also agree vertically; disagreement is an unsafe identity join.
    invalid_groups = set()
    for i, left in enumerate(panels):
        for right in panels[i+1:]:
            if left['group'] != right['group'] or not left['anchors'] or not right['anchors']:
                continue
            left_y = {n:y for n,y,_ in left['anchors']}
            right_y = {n:y for n,y,_ in right['anchors']}
            common = set(left_y) & set(right_y)
            if not common or any(abs(left_y[n]-right_y[n]) >= .01 for n in common):
                invalid_groups.add(left['group'])
    if invalid_groups:
        flags.append('AMBIGUOUS_IDENTITY_JOIN')
    for panel in panels:
        if panel['group'] in invalid_groups:
            continue
        anchors = panel['anchors']
        if not anchors and not panel['duplicate']:
            sibling = [p for p in panels if p is not panel and p['group'] == panel['group'] and p['anchors']]
            # Borrow only when every independent anchor source agrees in number
            # and vertical location; incompatible evidence makes the join unsafe.
            if sibling:
                candidate = sibling[0]['anchors']
                if all(len(s['anchors']) == len(candidate) and all(a[0] == b[0] and abs(a[1]-b[1]) < .01 for a,b in zip(s['anchors'], candidate)) for s in sibling[1:]):
                    anchors = candidate
        if not anchors:
            flags.append('MISSING_PRINTED_ROW'); continue
        parents.update(c[1] for c in panel['cols'].values() if c[1] in ('father_name','mother_name','combined_parent_name'))
        collected = _collect_words(panel['page'], panel['crop'], panel['cols'], anchors, panel['box'], panel['bottom'], panel['strict'])
        for number, fields in collected.items():
            for field, words in fields.items():
                words.sort()
                rows[number][field].append(' '.join(w[2] for w in words))
    result = {'schema_version':'1.0', 'layout_family':'unknown', 'household':dict.fromkeys(HOUSEHOLD_FIELDS),
              'members':[], 'field_evidence':[], 'review_flags':[]}
    if {'father_name','mother_name'} <= parents and 'combined_parent_name' not in parents:
        result['layout_family'] = 'modern'
    elif parents == {'combined_parent_name'}:
        result['layout_family'] = 'legacy'
    def assign(target, field, candidates, path):
        distinct = list(dict.fromkeys(v for v in candidates if v))
        if len(distinct) == 1:
            target[field] = distinct[0]
        elif len(distinct) > 1:
            result['review_flags'].append({'path':path,'code':'AMBIGUOUS_OCR_VALUES','message':'Multiple OCR values require source review.'})
    for field in HOUSEHOLD_FIELDS:
        path = 'household.'+field
        assign(result['household'], field, values[field], path)
        result['field_evidence'].append({'path':path,'status':'NOT_REVIEWED','section':'household'})
    for number in sorted(rows):
        member = dict.fromkeys(MEMBER_FIELDS); member['row_number'] = number
        idx = len(result['members'])
        for field in MEMBER_FIELDS:
            path = f'members[{idx}].{field}'
            if field != 'row_number': assign(member, field, rows[number][field], path)
            result['field_evidence'].append({'path':path,'status':'NOT_REVIEWED','section':'member','row_number':number})
        result['members'].append(member)
    for code in sorted(set(flags) | {'MISSING_OR_UNMAPPED_FIELDS'}):
        result['review_flags'].append({'path':'document','code':code,'message':'Review OCR mapping against the source.'})
    assert_valid_payload(result)
    return result, {'recognized_table_count':len(panels),'member_count':len(result['members']),
                    'populated_member_fields':sum(v is not None for m in result['members'] for k,v in m.items() if k != 'row_number')}
