"""Score complete KK extractions against independently supplied field references."""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from kkpilot.validation import HOUSEHOLD_FIELDS, MEMBER_FIELDS

EMPTY = {"blank", "absent"}


def normalized(field, value):
    if not isinstance(value, str):
        return value
    value = " ".join(value.upper().split())
    if field in {"nik_raw", "kk_number_raw"} and re.fullmatch(r"[0-9]+(?:[ .]+[0-9]+)*", value):
        return re.sub(r"[ .]", "", value)
    if field == "form_serial":
        return re.sub(r"^NO\.\s*", "", value)
    if field == "address":
        return re.sub(r"^KP(?:\.\s*|\s+)", "KP ", value)
    if field in {"father_name", "education"} and re.fullmatch(r"[A-Z](?: [A-Z])+", value):
        return value.replace(" ", "")
    return value


def fields(extraction):
    result = {}
    def add(prefix, values, names):
        for name in names:
            if name in values:
                result[prefix + name] = values[name]
    add("household.", extraction["household"], HOUSEHOLD_FIELDS)
    roster = []
    for member in extraction["members"]:
        n = member["row_number"]
        if type(n) is not int or n < 1 or n in roster:
            raise ValueError("Each member needs a distinct positive row_number")
        roster.append(n)
        add(f"members[{n}].", member, [k for k in MEMBER_FIELDS if k != "row_number"])
    return result, roster


def score(reference, prediction):
    if reference["source_sha256"] != prediction["source_sha256"]:
        raise ValueError("Prediction and reference must use the same source image")
    if not re.fullmatch("[0-9a-f]{64}", reference["source_sha256"]):
        raise ValueError("Invalid source digest")
    target, expected_rows = fields(reference["extraction"])
    actual, actual_rows = fields(prediction["extraction"])
    size = len(HOUSEHOLD_FIELDS) + len(expected_rows) * (len(MEMBER_FIELDS) - 1)
    states = reference["states"]
    if expected_rows != list(range(1, len(expected_rows) + 1)) or len(target) != size or set(states) != set(target):
        raise ValueError("Incomplete reference, roster or evidence-state coverage")
    counts = Counter(dict.fromkeys(("slots","readable","readable_matches","empty_or_absent","empty_or_absent_matches","matches","differences","unknown"),0))
    for path, expected in target.items():
        state = states[path]
        if state not in {"readable", "unknown", *EMPTY}:
            raise ValueError("Unexpected reference state")
        counts["slots"] += 1
        if state == "unknown":
            counts["unknown"] += 1
            continue
        if (state in EMPTY and expected is not None) or (state == "readable" and not isinstance(expected,str)):
            raise ValueError("Reference value does not agree with state")
        group = "empty_or_absent" if state in EMPTY else "readable"
        name = path.rsplit(".", 1)[1]
        counts[group] += 1
        if path in actual and normalized(name, actual[path]) == normalized(name, expected):
            counts["matches"] += 1
            counts[group + "_matches"] += 1
        else:
            counts["differences"] += 1
    layout_ok = reference["extraction"]["layout_family"] == prediction["extraction"]["layout_family"]
    counts["documents"] = 1
    counts["full_document_matches"] = int(layout_ok and actual_rows == expected_rows and counts["matches"] == size)
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    args = parser.parse_args()
    refs, preds = (json.loads(p.read_bytes()) for p in (args.reference, args.predictions))
    if not refs or set(refs) != set(preds):
        raise ValueError("Reference and prediction cohorts must match")
    per_document = {k: score(refs[k], preds[k]) for k in sorted(refs)}
    totals = Counter()
    for counts in per_document.values():
        totals.update(counts)
    print(json.dumps({"totals":dict(totals), "per_document":per_document}, indent=2))


if __name__ == "__main__":
    main()
