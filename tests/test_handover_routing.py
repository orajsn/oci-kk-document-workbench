import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/"examples"))
from tuning_lab import evaluate
from legacy_print import eligible_numbers,confirmed


class TuningTests(unittest.TestCase):
    def test_threshold_changes_false_pass_and_review_load(self):
        self.assertEqual(evaluate(.80),(0,1,0))
        self.assertEqual(evaluate(.90),(2,0,1))
        with self.assertRaises(ValueError):evaluate(1.2)

    def test_legacy_requires_unique_repeat_and_readable_crop(self):
        doc={"layout_family":"legacy","members":[{"nik_raw":"000. 000000. 0001"},{"nik_raw":"000. 000000. 0002"}]}
        vals=eligible_numbers(doc);stored=doc["members"][0]["nik_raw"]
        self.assertTrue(confirmed(vals,["invalid_format"],stored,"0000000000001","returned",False))
        self.assertFalse(confirmed(vals,["invalid_format"],stored,"0000000000001","returned",True))
        self.assertFalse(confirmed(vals,["invalid_format"],stored,"0000000000003","returned",False))
        self.assertFalse(eligible_numbers({**doc,"layout_family":"modern"}))
