"""Uniform OCR-corroborated NIK routing; retention is not source verification."""
import copy
import math
import re

VERSION = 'ocr-corroborated-nik-blood-2.0'


def _rows(payload):
    if type(payload) is not dict or type(payload.get('members')) is not list:
        raise ValueError('ROW_SET_INVALID')
    indexed = {}
    for row in payload['members']:
        if type(row) is not dict: raise ValueError('ROW_SET_INVALID')
        n = row.get('row_number')
        if type(n) is not int or not 1 <= n <= 100 or n in indexed: raise ValueError('ROW_SET_INVALID')
        for field in ('nik_raw', 'blood_group', 'full_name'):
            if field not in row or row[field] is not None and type(row[field]) is not str:
                raise ValueError('FIELD_INVALID')
        indexed[n] = row
    if not 1 <= len(indexed) <= 100: raise ValueError('ROW_SET_INVALID')
    return indexed


def _nik(value):
    if type(value) is not str: return None
    normalized = ''.join(value.split())
    return normalized if re.fullmatch(r'[0-9]{16}', normalized, flags=re.ASCII) else None


def _name(value):
    return ' '.join(value.split()) if type(value) is str and value.strip() else None


def _box(value):
    if (type(value) is not list or len(value) != 4
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in value)
            or value[0] >= value[2] or value[1] >= value[3]): return None
    return value


def _geometry(geometry):
    if type(geometry) is not dict or geometry.get('mapping') != 'RESIZE_ONLY': return None
    scale = geometry.get('scale'); nr = _box(geometry.get('name_rect')); ir = _box(geometry.get('identifier_rect'))
    if (type(scale) not in (tuple, list) or len(scale) != 2
            or any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in scale)
            or nr is None or ir is None): return None
    return scale, nr, ir


def _inside(box, rect):
    return rect[0] <= (box[0] + box[2]) / 2 <= rect[2] and rect[1] <= (box[1] + box[3]) / 2 <= rect[3]


def _overlap(a, b):
    intersection = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    union = max(a[3], b[3]) - min(a[1], b[1])
    return intersection / union >= 0.5


def project(current, hybrid, lines, geometry):
    """Return (new extraction, value-free row evidence); inputs are never mutated."""
    old, new = _rows(current), _rows(hybrid)
    if set(old) != set(new): raise ValueError('ROW_SET_MISMATCH')
    result = copy.deepcopy(current)
    evidence = []
    geo = _geometry(geometry)
    valid_lines = type(lines) is list and all(type(line) is dict and type(line.get('text')) is str for line in lines)
    mapped = []
    if geo and valid_lines:
        scale, _, _ = geo
        for index, line in enumerate(lines):
            box = _box(line.get('bbox_pixels'))
            box = [box[0]*scale[0], box[1]*scale[1], box[2]*scale[0], box[3]*scale[1]] if box else None
            mapped.append((index, line['text'], box))
    for member in result['members']:
        number = member['row_number']; baseline = old[number]; proposed = new[number]
        before = baseline['nik_raw']; after = proposed['nik_raw']
        entry = {'row_number': number, 'nik_status': 'HELD', 'reason': None,
                 'name_line_index': None, 'nik_line_index': None,
                 'blood_changed': baseline['blood_group'] != proposed['blood_group']}
        member['blood_group'] = proposed['blood_group']
        if before == after or (_nik(before) is not None and _nik(before) == _nik(after)):
            entry.update(nik_status='UNCHANGED', reason='SAME_DIGITS')
        elif _nik(after) is None:
            entry['reason'] = 'CANDIDATE_NIK_INVALID'
        elif geo is None or not valid_lines:
            entry['reason'] = 'GEOMETRY_OR_OCR_UNAVAILABLE'
        elif _name(baseline['full_name']) is None:
            entry['reason'] = 'BASELINE_NAME_UNAVAILABLE'
        else:
            _, name_rect, identifier_rect = geo
            name_matches = [(i, box) for i, text, box in mapped if box is not None
                            and _inside(box, name_rect) and _name(text) == _name(baseline['full_name'])]
            nik_matches = [(i, box) for i, text, box in mapped if box is not None
                           and _inside(box, identifier_rect) and _nik(text) == _nik(after)]
            unlocated_niks = any(box is None and _nik(text) is not None for _, text, box in mapped)
            if len(name_matches) != 1:
                entry['reason'] = 'NAME_NOT_UNIQUE_IN_ZONE'
            elif len(nik_matches) != 1:
                entry['reason'] = 'NIK_NOT_UNIQUE_IN_ZONE'
            elif unlocated_niks:
                entry['reason'] = 'UNLOCATED_NIK_AMBIGUITY'
            else:
                ni, name_box = name_matches[0]; vi, nik_box = nik_matches[0]
                entry['name_line_index'] = ni; entry['nik_line_index'] = vi
                conflicts = [i for i, text, box in mapped if i != vi and box is not None and _nik(text) is not None
                             and _inside(box, identifier_rect) and _overlap(box, name_box)]
                if ni == vi:
                    entry['reason'] = 'SAME_SOURCE_LINE_AMBIGUOUS'
                elif not _overlap(name_box, nik_box):
                    entry['reason'] = 'ROW_VERTICAL_MISMATCH'
                elif conflicts:
                    entry['reason'] = 'OVERLAPPING_NIK_AMBIGUITY'
                else:
                    member['nik_raw'] = after
                    entry.update(nik_status='ACCEPTED', reason='OCR_ROW_CORROBORATED')
        evidence.append(entry)
    return result, evidence
