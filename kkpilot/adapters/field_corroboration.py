"""Conservative field-position corroboration for OCR. No identity-specific rules.

A corroborated value is only geometrically associated with a label/name. This is
not a finding that the OCR or the value is true. Coordinates are pixel boxes.
Date candidates and complete OCR lines match by valid calendar date in the
explicitly supported four-digit-year formats, not necessarily by spelling.
OCR input stays unchanged; the projected field retains the candidate literal.
"""
from copy import deepcopy
from datetime import date
import re

_MONTHS = {name: i for i, name in enumerate(("januari", "februari", "maret", "april", "mei", "juni", "juli", "agustus", "september", "oktober", "november", "desember"), 1)}
_HEADERS = {"alamat", "dikeluarkan tanggal", "nama lengkap", "nik", "tempat lahir", "tanggal lahir", "jenis kelamin", "agama", "pendidikan", "pekerjaan", "status perkawinan", "kewarganegaraan", "nomor", "rt rw", "desa kelurahan", "kecamatan", "kabupaten kota", "provinsi", "kode pos"}


def _norm(value):
    return " ".join(value.casefold().split()) if isinstance(value, str) else ""


def _date(value):
    s = _norm(value)
    m = re.fullmatch(r"(\d{2})-(\d{2})-(\d{4})", s)
    if m:
        d, mo, y = map(int, m.groups())
    else:
        m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s)
        if m:
            y, mo, d = map(int, m.groups())
        else:
            m = re.fullmatch(r"(\d{1,2})\s+([a-z]+)\s+(\d{4})", s)
            if not m or m.group(2) not in _MONTHS:
                return None
            d, mo, y = int(m.group(1)), _MONTHS[m.group(2)], int(m.group(3))
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def _box(line):
    if not isinstance(line, dict):
        return None
    b = line.get("bbox_pixels")
    if not isinstance(b, (list, tuple)) or len(b) != 4 or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in b):
        return None
    import math
    if not all(math.isfinite(v) for v in b) or b[2] <= b[0] or b[3] <= b[1]:
        return None
    return tuple(b)


def _same_line(a, b):
    # Vertical centers at most half the mean box height apart.
    return abs((a[1] + a[3]) / 2 - (b[1] + b[3]) / 2) <= ((a[3] - a[1]) + (b[3] - b[1])) / 4


def _labels(lines, label):
    result = []
    for i, line in enumerate(lines):
        if _norm(line.get("text") if isinstance(line, dict) else None).strip(" :") == label:
            result.append((i, _box(line)))
    # A split issue-date label is accepted only for adjacent OCR entries.
    if label == "dikeluarkan tanggal":
        for i in range(len(lines) - 1):
            a, b = lines[i], lines[i + 1]
            if not isinstance(a, dict) or not isinstance(b, dict):
                continue
            if _norm(a.get("text")).strip(" :") == "dikeluarkan" and _norm(b.get("text")).strip(" :") == "tanggal":
                x, y = _box(a), _box(b)
                if x and y and x != y and y[0] >= x[2] and _same_line(x, y):
                    result.append(((i, i + 1), (x[0], min(x[1], y[1]), y[2], max(x[3], y[3]))))
    return result


def _matching(lines, candidate, is_date):
    expected = _date(candidate) if is_date else _norm(candidate)
    if not expected:
        return []
    found = []
    for i, line in enumerate(lines):
        text = line.get("text") if isinstance(line, dict) else None
        actual = _date(text) if is_date else _norm(text)
        if actual is not None and actual == expected:
            found.append(i)
    return found


def _continuation_or_header(lines, anchor, value, excluded):
    # Conservatively hold when another text box occupies the space between,
    # continues to the right on the same line, or starts directly below value.
    for i, line in enumerate(lines):
        if i in excluded or not isinstance(line, dict) or not _norm(line.get("text")):
            continue
        b = _box(line)
        if not b:
            continue
        if _same_line(value, b):
            if b[0] >= anchor[2] and b[2] <= value[0] and _norm(line["text"]).strip(" :") in _HEADERS:
                return "competing_header"
            if b[0] >= value[2] and b[0] - value[2] <= max(value[3] - value[1], b[3] - b[1]) * 2:
                return "possible_continuation"
        height = value[3] - value[1]
        if b[1] >= value[3] and b[1] - value[3] <= height * 0.5 and abs(b[0] - value[0]) <= height and _norm(line["text"]).strip(" :") not in _HEADERS:
            return "possible_continuation"
    return None


def project(current, hybrid, lines):
    """Return (deep-copied projected payload, value-free evidence list).

    Each evidence entry contains only path, status, reason, and OCR indices.
    Mismatched or invalid member rowsets disable all changes, including household.
    """
    output = deepcopy(current)
    evidence = []
    if not isinstance(current, dict) or not isinstance(hybrid, dict) or not isinstance(lines, list):
        return output, [{"path": "$", "status": "hold", "reason": "invalid_input", "indices": []}]
    cm, hm = current.get("members"), hybrid.get("members")
    def rows(members):
        if not isinstance(members, list) or any(not isinstance(m, dict) or isinstance(m.get("row_number"), bool) or not isinstance(m.get("row_number"), int) for m in members):
            return None
        ids = [m["row_number"] for m in members]
        return ids if len(ids) == len(set(ids)) else None
    cr, hr = rows(cm), rows(hm)
    if cr is None or hr is None or set(cr) != set(hr):
        return output, [{"path": "members", "status": "hold", "reason": "rowset_mismatch", "indices": []}]
    candidate_rows = {m["row_number"]: m for m in hm}
    name_anchors = []
    for m in cm:
        name = _norm(m.get("full_name"))
        matches = _matching(lines, name, False) if name else []
        name_anchors.append(matches[0] if len(matches) == 1 else None)

    def assess(path, old, candidate, anchors, is_date, member_index=None):
        def record(status, reason, indices=()):
            evidence.append({"path": path, "status": status, "reason": reason, "indices": list(indices)})
        if not isinstance(candidate, str) or not _norm(candidate) or (is_date and _date(candidate) is None):
            record("hold", "invalid_candidate")
            return old
        if candidate == old:
            record("hold", "unchanged")
            return old
        matches = _matching(lines, candidate, is_date)
        if len(matches) != 1:
            record("hold", "candidate_not_unique", matches)
            return old
        if len(anchors) != 1:
            record("hold", "anchor_not_unique", matches)
            return old
        ai, ab = anchors[0]
        vi = matches[0]
        vb = _box(lines[vi])
        anchor_indices = list(ai) if isinstance(ai, tuple) else [ai]
        indices = anchor_indices + [vi]
        if any(isinstance(line, dict) and _norm(line.get("text")) and not _box(line) for line in lines):
            record("hold", "geometry", indices)
            return old
        if not ab or not vb or ab == vb or vi in anchor_indices or vb[0] < ab[2] or not _same_line(ab, vb):
            record("hold", "geometry", indices)
            return old
        if member_index is not None:
            own_gap = vb[0] - ab[2]
            for j, ni in enumerate(name_anchors):
                if j == member_index:
                    continue
                if ni is None or not _box(lines[ni]):
                    record("hold", "other_member_anchor_uncertain", indices)
                    return old
                nb = _box(lines[ni])
                if nb and nb != vb and vb[0] >= nb[2] and _same_line(nb, vb) and vb[0] - nb[2] <= own_gap:
                    record("hold", "closer_member_anchor", indices + [ni])
                    return old
        reason = _continuation_or_header(lines, ab, vb, set(indices))
        if reason:
            record("hold", reason, indices)
            return old
        record("applied", "geometric_corroboration", indices)
        return candidate

    ch, hh = current.get("household"), hybrid.get("household")
    if isinstance(ch, dict) and isinstance(hh, dict):
        for field, label, is_date in (("address", "alamat", False), ("issued_date_raw", "dikeluarkan tanggal", True)):
            value = assess("household." + field, ch.get(field), hh.get(field), _labels(lines, label), is_date)
            if evidence[-1]["status"] == "applied":
                output["household"][field] = value
    for i, member in enumerate(cm):
        matches = _matching(lines, member.get("full_name"), False) if _norm(member.get("full_name")) else []
        anchors = [(matches[0], _box(lines[matches[0]]))] if len(matches) == 1 else []
        candidate = candidate_rows[member["row_number"]].get("birth_date_raw")
        value = assess(f"members[{i}].birth_date_raw", member.get("birth_date_raw"), candidate, anchors, True, i)
        if evidence[-1]["status"] == "applied":
            output["members"][i]["birth_date_raw"] = value
    return output, evidence
