"""Job-level queue counts and compatibility with installations without NIK review tables."""
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

from kkpilot.cloud.review_repository import CloudReviewRepository


class SampleDatabaseError(Exception):
    pass


def error(code):
    return SampleDatabaseError(SimpleNamespace(code=code))


class BatchReviewSummaryTests(TestCase):
    def setUp(self):
        self.repository = CloudReviewRepository(
            lambda: None, SimpleNamespace(DatabaseError=SampleDatabaseError), "training", "reviewer"
        )
        session = MagicMock()
        self.cursor = session.__enter__.return_value.cursor.return_value.__enter__.return_value
        self.enterContext(patch.object(self.repository, "_session", return_value=session))
        self.row = dict(
            id="card", case_id="batch-001", filename="training.png", sha256="f"*64,
            status="NEEDS_REVIEW", version=1, attempts=1, max_attempts=1,
            last_error=None, created_at=None, updated_at=None, result_available=1, member_count=2
        )

    def test_open_case_is_independent_of_document_sign_off(self):
        self.cursor.fetchone.side_effect = [dict(self.row, nik_open_count=0, nik_first_open_key=None), None]
        result = self.repository.list_jobs()
        self.assertTrue(result[0]["result_available"])
        self.assertEqual("NEEDS_REVIEW", result[0]["status"])
        self.assertEqual(0, result[0]["nik_open_count"])
        self.assertIsNone(result[0]["nik_first_open_key"])

    def test_review_installation_returns_one_entry_point_for_the_document(self):
        key = "a"*24
        self.cursor.fetchone.side_effect = [dict(self.row, nik_open_count=2, nik_first_open_key=key), None]
        [job] = self.repository.list_jobs()
        self.assertEqual((2, key), (job["nik_open_count"], job["nik_first_open_key"]))

    def test_missing_optional_tables_keeps_card_visible_and_count_unknown(self):
        self.cursor.execute.side_effect = [error(942), None]
        self.cursor.fetchone.side_effect = [dict(self.row, nik_open_count=None, nik_first_open_key=None), None]
        [job] = self.repository.list_jobs(search="batch-001")
        self.assertIsNone(job["nik_open_count"])
        first, fallback = self.cursor.execute.call_args_list
        self.assertEqual(first.args[1], fallback.args[1])
        self.assertEqual("training", fallback.args[1]["scope_id"])
        self.assertNotIn("kk_nik_review_cases", fallback.args[0].lower())

    def test_unrelated_database_errors_remain_visible(self):
        self.cursor.execute.side_effect = error(3113)
        with self.assertRaises(SampleDatabaseError):
            self.repository.list_jobs()
        self.assertEqual(1, self.cursor.execute.call_count)
