"""Conservative, review-required mapping of Document Understanding OCR output."""
from __future__ import annotations

import re
from collections import defaultdict
from ..validation import HOUSEHOLD_FIELDS, MEMBER_FIELDS, assert_valid_payload


class DUParseError(ValueError):
    """Safe, non-PII processing error; retain the original response separately."""
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _norm(s):
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


HEADERS = {
    'NO': 'row_number', 'NOMOR': 'row_number', 'NOURUT': 'row_number', 'NOMORURUT': 'row_number',
    'NAMALENGKAP': 'full_name', 'NIK': 'nik_raw', 'JENISKELAMIN': 'sex',
    'TEMPATLAHIR': 'birth_place', 'TANGGALLAHIR': 'birth_date_raw', 'AGAMA': 'religion',
    'PENDIDIKAN': 'education', 'JENISPEKERJAAN': 'occupation', 'PEKERJAAN': 'occupation',
    'GOLONGANDARAH': 'blood_group', 'STATUSPERKAWINAN': 'marital_status',
    'TANGGALPERKAWINAN': 'marriage_date_raw', 'STATUSHUBUNGANDALAMKELUARGA': 'relationship_to_head',
    'HUBUNGANDALAMKELUARGA': 'relationship_to_head', 'KEWARGANEGARAAN': 'nationality',
    'NOPASPOR': 'passport_number', 'NOMORPASPOR': 'passport_number', 'NOKITASKITAP': 'residence_permit_number',
    'AYAH': 'father_name', 'NAMAAYAH': 'father_name', 'NAMAORANGTUAAYAH': 'father_name',
    'IBU': 'mother_name', 'NAMAIBU': 'mother_name', 'NAMAORANGTUAIBU': 'mother_name',
    'NAMAORANGTUA': 'combined_parent_name',
}
HOUSE = {
    'NAMAKEPALAKELUARGA': 'head_name', 'ALAMAT': 'address', 'RT': 'rt', 'RW': 'rw',
    'DESAKELURAHAN': 'village', 'KELURAHANDESA': 'village', 'KECAMATAN': 'district',
    'KABUPATENKOTA': 'regency_city', 'KOTAKABUPATEN': 'regency_city', 'PROVINSI': 'province',
    'KODEPOS': 'postal_code', 'TANGGALDIKELUARKAN': 'issued_date_raw', 'NOSERI': 'form_serial',
}


def _box(item):
    vertices = (item.get('bounding_polygon') or {}).get('normalized_vertices') or []
    if not vertices or any('x' not in p or 'y' not in p for p in vertices):
        return None
    return (min(p['x'] for p in vertices), min(p['y'] for p in vertices),
            max(p['x'] for p in vertices), max(p['y'] for p in vertices))


def _columns(table):
    parts = defaultdict(list)
    for row in table.get('header_rows') or []:
        for cell in row.get('cells') or []:
            if isinstance(cell.get('column_index'), int) and cell.get('text'):
                parts[cell['column_index']].append(cell['text'])
    mapped = {}
    for col, texts in parts.items():
        full = _norm(' '.join(texts))
        fields = {HEADERS[_norm(t)] for t in texts if _norm(t) in HEADERS}
        if full in HEADERS:
            fields.add(HEADERS[full])
            if HEADERS[full] != 'row_number':
                fields.discard('row_number')
        if 'combined_parent_name' in fields and fields & {'father_name', 'mother_name'}:
            fields.discard('combined_parent_name')
        if len(fields) == 1:
            mapped[col] = fields.pop()
    return mapped


def _prepare_table(table):
    if table.get('header_rows'):
        return _columns(table), table.get('body_rows') or []
    body = table.get('body_rows') or []
    found = None
    for count in range(1, min(3, len(body)) + 1):
        mapping = _columns({'header_rows': body[:count]})
        fields = set(mapping.values()) - {'row_number'}
        if len(fields) >= 2 and (fields & {'full_name', 'nik_raw', 'father_name', 'mother_name', 'combined_parent_name'} or len(fields) >= 3):
            found = count
            break
    if found is None:
        return {}, body
    # Additional header rows contain recognized labels, not just numeric captions.
    while found < min(3, len(body)):
        following = _columns({'header_rows': [body[found]]})
        if not (set(following.values()) - {'row_number'}):
            break
        found += 1
    return _columns({'header_rows': body[:found]}), body[found:]


def _external_number(page, table, cells):
    boxes = [_box(c) for c in cells if (c.get('text') or '').strip() and _box(c)]
    if not boxes:
        raise DUParseError('MISSING_ROW_GEOMETRY')
    left = min(b[0] for b in boxes)
    y1 = min(b[1] for b in boxes)
    y2 = max(b[3] for b in boxes)
    candidates = []
    for word in page.get('words') or []:
        text = word.get('text') or ''
        box = _box(word)
        if not box or not re.fullmatch(r'[0-9]{1,3}', text.strip()):
            continue
        n = int(text.strip())
        center_y = (box[1] + box[3]) / 2
        if 1 <= n <= 100 and box[2] <= left and left - box[2] <= .12 and y1 <= center_y <= y2:
            candidates.append(n)
    if len(candidates) != 1:
        raise DUParseError('AMBIGUOUS_PRINTED_ROW')
    return candidates[0]


def _is_caption(cells, mapping, leading=False):
    # A printed column-number caption is a complete 1..N sequence across columns.
    ordered = sorted(cells, key=lambda c: c.get('column_index', -1))
    values = [str(c.get('text') or '').strip() for c in ordered]
    if len(values) >= 3 and values == [str(i) for i in range(1, len(values) + 1)]:
        return True
    if leading and len(values) >= 3 and all(re.fullmatch(r'\([0-9]{1,2}\)', v) for v in values):
        numbers = [int(v[1:-1]) for v in values]
        return numbers == list(range(numbers[0], numbers[0] + len(numbers)))
    return False


def _household(page, first_y, values):
    lines = page.get('lines') or []
    title = any('KARTUKELUARGA' in _norm(x.get('text')) and (_box(x) is None or _box(x)[1] < first_y) for x in lines)
    candidates = []
    for line in lines:
        text = line.get('text') or ''
        box = _box(line)
        if box and box[1] >= first_y:
            continue
        match = re.match(r'^\s*([^:]+?)\s*:\s*(.*?)\s*$', text)
        if match:
            key, value = _norm(match.group(1)), match.group(2)
            field = HOUSE.get(key) or ('kk_number_raw' if title and key in ('NO', 'NOMOR') else None)
            if field and value:
                values[field].append(value)
            continue
        key = _norm(text)
        field = HOUSE.get(key) or ('kk_number_raw' if title and key in ('NO', 'NOMOR') else None)
        if field and box:
            nearby = []
            for other in lines:
                ob = _box(other)
                ot = other.get('text') or ''
                if other is line or not ob or not ot.strip() or ob[1] >= first_y or _norm(ot) in HOUSE:
                    continue
                if ob[0] >= box[2] and abs((ob[1]+ob[3]-box[1]-box[3])/2) <= max(box[3]-box[1], ob[3]-ob[1]) * 0.6:
                    nearby.append((ob[0]-box[2], ot))
            if nearby:
                nearby.sort(key=lambda pair: pair[0])
                # Select only an unambiguous closest line.
                if len(nearby) == 1 or nearby[1][0] - nearby[0][0] > 0.02:
                    values[field].append(nearby[0][1])
                else:
                    values[field].extend([nearby[0][1], nearby[1][1]])


def parse_du_response(raw):
    """Return (canonical extraction, safe mapping metadata); never mark OCR reviewed."""
    if not isinstance(raw, dict) or not isinstance(raw.get('pages'), list):
        raise DUParseError('INVALID_DU_RESPONSE')
    result = {'schema_version': '1.0', 'layout_family': 'unknown',
              'household': dict.fromkeys(HOUSEHOLD_FIELDS), 'members': [],
              'field_evidence': [], 'review_flags': []}
    values = defaultdict(list)
    rows = defaultdict(lambda: defaultdict(list))
    table_rows = []
    parent_fields = set()
    recognized_tables = 0
    unmapped_columns = 0
    for page in raw['pages']:
        tables = page.get('tables') or []
        boxes = [_box(t) for t in tables]
        first_y = min((b[1] for b in boxes if b), default=1.0)
        _household(page, first_y, values)
        for table in tables:
            mapping, body = _prepare_table(table)
            if not mapping or set(mapping.values()) == {'row_number'}:
                if any(any((c.get('text') or '').strip() for c in r.get('cells') or []) for r in body):
                    raise DUParseError('UNMAPPABLE_TABLE')
                continue
            recognized_tables += 1
            parent_fields.update(set(mapping.values()) & {'father_name', 'mother_name', 'combined_parent_name'})
            row_cols = [c for c, field in mapping.items() if field == 'row_number']
            if len(row_cols) > 1:
                # A second No heading is often a document number, not a row ID.
                if 0 in row_cols:
                    row_cols = [0]
                else:
                    raise DUParseError('AMBIGUOUS_ROW_HEADING')
            seen = set()
            for row in body:
                cells = row.get('cells') or []
                if _is_caption(cells, mapping, leading=not seen):
                    continue
                populated = [c for c in cells if isinstance(c.get('text'), str) and c['text'].strip()]
                if not populated:
                    continue
                nonrow = [c for c in populated if not row_cols or c.get('column_index') != row_cols[0]]
                if not any(c['text'].strip() not in ('-', '–', '—') for c in nonrow):
                    continue
                if row_cols:
                    nums = [c['text'].strip() for c in populated if c.get('column_index') == row_cols[0]]
                    if len(nums) != 1 or not re.fullmatch(r'[0-9]{1,3}', nums[0]) or not 1 <= int(nums[0]) <= 100:
                        raise DUParseError('INVALID_PRINTED_ROW')
                    num = int(nums[0])
                else:
                    num = _external_number(page, table, cells)
                if num in seen:
                    raise DUParseError('DUPLICATE_PRINTED_ROW')
                seen.add(num)
                for cell in populated:
                    col = cell.get('column_index')
                    if row_cols and col == row_cols[0]:
                        continue
                    field = mapping.get(col)
                    if not field or field == 'row_number':
                        unmapped_columns += 1
                        continue
                    rows[num][field].append(cell['text'])
                rows[num]  # Retain a printed row even if its fields were unmapped.
            table_rows.append(seen)
    if len(table_rows) > 1 and any(s != table_rows[0] for s in table_rows[1:]):
        raise DUParseError('UNMATCHED_PRINTED_ROWS')
    if not recognized_tables:
        raise DUParseError('NO_MEMBER_TABLE')
    if parent_fields == {'father_name', 'mother_name'}:
        result['layout_family'] = 'modern'
    elif parent_fields == {'combined_parent_name'}:
        result['layout_family'] = 'legacy'
    def assign(target, field, candidates, path):
        distinct = list(dict.fromkeys(candidates))
        if len(distinct) == 1:
            target[field] = distinct[0]
        elif len(distinct) > 1:
            result['review_flags'].append({'path': path, 'code': 'AMBIGUOUS_OCR_VALUES', 'message': 'Multiple OCR values require source review.'})
    for field in HOUSEHOLD_FIELDS:
        assign(result['household'], field, values[field], 'household.' + field)
        result['field_evidence'].append({'path': 'household.' + field, 'status': 'NOT_REVIEWED', 'section': 'household'})
    for num in sorted(rows):
        member = dict.fromkeys(MEMBER_FIELDS)
        member['row_number'] = num
        idx = len(result['members'])
        for field in MEMBER_FIELDS:
            path = f'members[{idx}].{field}'
            if field != 'row_number':
                assign(member, field, rows[num][field], path)
            result['field_evidence'].append({'path': path, 'status': 'NOT_REVIEWED', 'section': 'member', 'row_number': num})
        result['members'].append(member)
    if any(value is None for value in result['household'].values()) or any(any(member[field] is None for field in MEMBER_FIELDS if field != 'row_number') for member in result['members']):
        result['review_flags'].append({'path': 'document', 'code': 'MISSING_OR_UNMAPPED_FIELDS', 'message': 'Review missing and unmapped fields against the source.'})
    if unmapped_columns:
        result['review_flags'].append({'path': 'document', 'code': 'UNMAPPED_OCR_COLUMN', 'message': 'Review OCR columns that could not be mapped.'})
    if result['layout_family'] == 'unknown':
        result['review_flags'].append({'path': 'layout_family', 'code': 'LAYOUT_UNCERTAIN', 'message': 'Review the parent headings and layout against the source.'})
    assert_valid_payload(result)
    return result, {'recognized_table_count': recognized_tables, 'member_count': len(result['members']), 'unmapped_cell_count': unmapped_columns}
