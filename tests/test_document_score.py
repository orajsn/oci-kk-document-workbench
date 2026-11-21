import copy
import json
from pathlib import Path
import sys
import unittest
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/"examples"))
from score_documents import score


class DocumentScoreTests(unittest.TestCase):
    def setUp(self):
        self.ref=json.loads((root/"examples/synthetic-reference.json").read_bytes())["example-card"]
        self.pred=copy.deepcopy(self.ref)
        del self.pred["states"]

    def test_complete_match(self):
        result=score(self.ref,self.pred)
        self.assertEqual((result["matches"],result["slots"],result["full_document_matches"]),(48,48,1))

    def test_gap_is_not_a_difference_or_a_full_match(self):
        self.ref["states"]["household.address"]="unknown"
        result=score(self.ref,self.pred)
        self.assertEqual((result["unknown"],result["differences"],result["full_document_matches"]),(1,0,0))

    def test_missing_empty_field_counts_as_difference(self):
        del self.pred["extraction"]["household"]["postal_code"]
        self.assertEqual(score(self.ref,self.pred)["differences"],1)

    def test_degree_of_formatting_and_different_source(self):
        self.ref["extraction"]["members"][0]["nik_raw"]="000. 000000. 0001"
        self.pred["extraction"]["members"][0]["nik_raw"]="0000000000001"
        self.assertEqual(score(self.ref,self.pred)["matches"],48)
        self.pred["source_sha256"]="0"*64
        with self.assertRaises(ValueError):score(self.ref,self.pred)

    def test_extra_or_duplicate_row(self):
        rows=self.pred["extraction"]["members"]
        rows.append({**rows[1],"row_number":3})
        self.assertEqual(score(self.ref,self.pred)["full_document_matches"],0)
        rows[-1]["row_number"]=1
        with self.assertRaises(ValueError):score(self.ref,self.pred)
