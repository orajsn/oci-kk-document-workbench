import copy
import io
import unittest
from contextlib import redirect_stdout

from examples.check_day_one import CARDS, load_json, report


def exports():
    records = []
    for index, card in enumerate(("A", "B"), 1):
        frozen = load_json(CARDS / "reader" / f"KK-{card}.json")
        records.append({
            "job": {"id": str(index), "case_id": f"TRAIN-00{index}", "version": 3,
                    "sha256": frozen["source_sha256"]},
            "review_history": [], "extraction": frozen["extraction"],
        })
    return records


class DayOneTests(unittest.TestCase):
    def test_completion_requires_the_saved_correction(self):
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(ValueError, "Koreksi NIK"):
            report(exports(), [.90], require_review=True)

    def test_thresholds_use_the_original_snapshot_after_review(self):
        batch = exports()
        doc = batch[1]
        doc["review_history"].append({"previous_extraction": copy.deepcopy(doc["extraction"])})
        doc["extraction"]["members"][1]["nik_raw"] = load_json(CARDS / "print-key.json")[doc["job"]["sha256"]]["2"]
        out = io.StringIO()
        with redirect_stdout(out):
            report(batch, [.90, .83, .80, .85], require_review=True)
        self.assertIn("awal 2/3, tersimpan 3/3", out.getvalue())
        table = {parts[0]: list(map(int, parts[1:])) for line in out.getvalue().splitlines()
                 if line.startswith("0.") for parts in [[s.strip() for s in line.split("|")]]}
        self.assertEqual(table, {"0.90": [2,0,1], "0.83": [1,0,0], "0.80": [0,1,0], "0.85": [2,0,1]})

    def test_rejects_duplicate_source(self):
        batch = exports()
        with self.assertRaisesRegex(ValueError, "Hash"):
            report([batch[0], batch[0]], [.90], False)
