"""Durability, fencing and review tests for the local-only job repository.

All identities and extracted values in this suite are synthetic. The tests use
temporary databases and make no network calls.
"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
import uuid

from kkpilot.repository import DomainError, Repository
from kkpilot.validation import PayloadError


HOUSEHOLD_FIELDS = (
    "kk_number_raw", "form_serial", "head_name", "address", "rt", "rw",
    "village", "district", "regency_city", "province", "postal_code",
    "issued_date_raw",
)
MEMBER_FIELDS = (
    "row_number", "full_name", "nik_raw", "sex", "birth_place",
    "birth_date_raw", "religion", "education", "occupation", "blood_group",
    "marital_status", "marriage_date_raw", "relationship_to_head",
    "nationality", "passport_number", "residence_permit_number", "father_name",
    "mother_name", "combined_parent_name",
)


def payload():
    household = dict.fromkeys(HOUSEHOLD_FIELDS)
    household.update(kk_number_raw="0000000000000000", head_name="SYNTHETIC TEST")
    member = dict.fromkeys(MEMBER_FIELDS)
    member.update(row_number=1, full_name="SYNTHETIC TEST", nik_raw="1111111111111111")
    evidence = [
        {"path": f"household.{field}", "status": "READABLE" if value is not None and value != "" else "BLANK_ON_DOCUMENT",
         "section": "household"}
        for field, value in household.items()
    ]
    evidence.extend(
        {"path": f"members[0].{field}", "status": "READABLE" if value is not None and value != "" else "BLANK_ON_DOCUMENT",
         "section": "members", "row_number": 1}
        for field, value in member.items()
    )
    return {
        "schema_version": "1.0", "layout_family": "modern",
        "household": household, "members": [member],
        "field_evidence": evidence, "review_flags": [],
    }


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db_path = Path(self.temp.name) / "jobs.db"
        self.repo = Repository(self.db_path)
        self.repo.initialize()

    def create(self, **overrides):
        values = {
            "case_id": "synthetic-case-a", "filename": "synthetic.png",
            "mode": "demo", "image_relpath": f"{uuid.uuid4().hex}.png",
            "sha256": "a" * 64, "idempotency_key": uuid.uuid4().hex,
            "request_hash": "b" * 64, "max_attempts": 3,
        }
        values.update(overrides)
        return self.repo.create_job(**values)

    def complete(self):
        detail, _ = self.create()
        claimed = self.repo.claim_job()
        self.assertEqual(detail["job"]["id"], claimed["id"])
        self.assertTrue(self.repo.complete_job(
            claimed["id"], claimed["lease_token"], payload(), {"issues": []}
        ))
        return self.repo.get_job(claimed["id"])

    def expire_lease(self, job_id):
        # Simulates a process dying after claiming, without clock sleeps.
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute("UPDATE jobs SET lease_expires = 0 WHERE id = ?", (job_id,))
            connection.commit()

    def test_idempotency_replays_existing_job_without_resetting_state(self):
        first, created = self.create(idempotency_key="same-upload")
        self.assertTrue(created)
        claimed = self.repo.claim_job()
        replay, created = self.create(idempotency_key="same-upload")
        self.assertFalse(created)
        self.assertEqual(first["job"]["id"], replay["job"]["id"])
        self.assertEqual("PROCESSING", replay["job"]["status"])
        self.assertEqual(claimed["version"], replay["job"]["version"])
        self.assertEqual(1, len(self.repo.list_jobs()))

    def test_idempotency_conflict_rejects_changed_request(self):
        first, _ = self.create(idempotency_key="same-upload")
        with self.assertRaises(DomainError) as raised:
            self.create(idempotency_key="same-upload", request_hash="c" * 64)
        self.assertEqual(409, raised.exception.status)
        unchanged = self.repo.get_job(first["job"]["id"])
        self.assertEqual("QUEUED", unchanged["job"]["status"])

    def test_idempotency_is_scoped_to_case(self):
        first, _ = self.create(idempotency_key="same-upload", case_id="case-a")
        second, created = self.create(idempotency_key="same-upload", case_id="case-b")
        self.assertTrue(created)
        self.assertNotEqual(first["job"]["id"], second["job"]["id"])

    def test_concurrent_duplicate_uploads_create_one_durable_job(self):
        barrier = threading.Barrier(8)

        def create():
            barrier.wait(timeout=10)
            return self.create(idempotency_key="simultaneous-upload")

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: create(), range(8)))
        self.assertEqual(1, sum(created for _, created in results))
        self.assertEqual(1, len({detail["job"]["id"] for detail, _ in results}))
        self.assertEqual(1, len(self.repo.list_jobs()))

    def test_concurrent_claims_give_each_job_to_one_worker(self):
        ids = {self.create()[0]["job"]["id"] for _ in range(8)}
        barrier = threading.Barrier(12)

        def claim():
            barrier.wait(timeout=10)
            return self.repo.claim_job()

        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(lambda _: claim(), range(12)))
        claimed = [result for result in results if result is not None]
        self.assertEqual(ids, {job["id"] for job in claimed})
        self.assertEqual(8, len(claimed))
        self.assertEqual(8, len({job["lease_token"] for job in claimed}))
        self.assertTrue(all(job["attempts"] == 1 for job in claimed))

    def test_expired_lease_is_reclaimed_and_old_worker_is_fenced(self):
        detail, _ = self.create()
        first = self.repo.claim_job()
        self.expire_lease(first["id"])
        second = self.repo.claim_job()
        self.assertEqual(detail["job"]["id"], second["id"])
        self.assertNotEqual(first["lease_token"], second["lease_token"])
        self.assertGreater(second["version"], first["version"])
        self.assertEqual(2, second["attempts"])
        self.assertFalse(self.repo.complete_job(
            first["id"], first["lease_token"], payload(), {"issues": []}
        ))
        self.assertFalse(self.repo.fail_job(first["id"], first["lease_token"], "STALE"))
        self.assertFalse(self.repo.heartbeat(first["id"], first["lease_token"], 120))
        self.assertTrue(self.repo.complete_job(
            second["id"], second["lease_token"], payload(), {"issues": []}
        ))
        self.assertEqual("NEEDS_REVIEW", self.repo.get_job(second["id"])["job"]["status"])

    def test_heartbeat_extends_only_current_lease(self):
        self.create()
        claimed = self.repo.claim_job(lease_seconds=60)
        self.assertTrue(self.repo.heartbeat(claimed["id"], claimed["lease_token"], 120))
        self.assertFalse(self.repo.heartbeat(claimed["id"], "unrelated-token", 120))
        self.assertIsNone(self.repo.claim_job())

    def test_expired_worker_cannot_finish_even_before_another_worker_claims(self):
        self.create()
        claimed = self.repo.claim_job()
        self.expire_lease(claimed["id"])
        self.assertFalse(self.repo.complete_job(
            claimed["id"], claimed["lease_token"], payload(), {"issues": []}
        ))
        self.assertFalse(self.repo.heartbeat(claimed["id"], claimed["lease_token"], 120))

    def test_retryable_failure_exhausts_attempts_without_infinite_requeue(self):
        detail, _ = self.create(max_attempts=2)
        first = self.repo.claim_job()
        self.assertTrue(self.repo.fail_job(first["id"], first["lease_token"], "TRANSIENT"))
        second = self.repo.claim_job()
        self.assertEqual(2, second["attempts"])
        self.assertTrue(self.repo.fail_job(second["id"], second["lease_token"], "TRANSIENT"))
        self.assertIsNone(self.repo.claim_job())
        failed = self.repo.get_job(detail["job"]["id"])
        self.assertEqual("FAILED", failed["job"]["status"])
        with self.assertRaises(DomainError):
            self.repo.retry_job(detail["job"]["id"])

    def test_nonretryable_failure_requires_explicit_retry_and_preserves_limit(self):
        detail, _ = self.create(max_attempts=2)
        first = self.repo.claim_job()
        self.assertTrue(self.repo.fail_job(
            first["id"], first["lease_token"], "INVALID_RESULT", retryable=False
        ))
        self.assertIsNone(self.repo.claim_job())
        retried = self.repo.retry_job(detail["job"]["id"])
        self.assertEqual("QUEUED", retried["job"]["status"])
        self.assertEqual(1, retried["job"]["attempts"])
        second = self.repo.claim_job()
        self.assertEqual(2, second["attempts"])

    def test_expired_last_attempt_becomes_failed(self):
        detail, _ = self.create(max_attempts=1)
        claimed = self.repo.claim_job()
        self.expire_lease(claimed["id"])
        self.assertIsNone(self.repo.claim_job())
        self.assertEqual("FAILED", self.repo.get_job(detail["job"]["id"])["job"]["status"])

    def test_job_and_extraction_survive_repository_reopen(self):
        detail = self.complete()
        reopened = Repository(self.db_path)
        reopened.initialize()
        restored = reopened.get_job(detail["job"]["id"])
        self.assertEqual(detail, restored)
        with closing(sqlite3.connect(self.db_path)) as connection:
            self.assertEqual("wal", connection.execute("PRAGMA journal_mode").fetchone()[0])

    def test_oversized_extraction_is_rejected_before_any_result_is_persisted(self):
        self.create()
        claimed = self.repo.claim_job()
        before = self.repo.get_job(claimed["id"])
        candidate = payload()
        candidate["members"] = []
        for row in range(1, 101):
            member = dict.fromkeys(MEMBER_FIELDS, "S" * 1800)
            member["row_number"] = row
            member["nik_raw"] = f"{row:016d}"
            candidate["members"].append(member)
        with self.assertRaises(PayloadError):
            self.repo.complete_job(claimed["id"], claimed["lease_token"], candidate, {"issues": []})
        after = self.repo.get_job(claimed["id"])
        self.assertEqual(before, after)
        self.assertIsNone(after["extraction"])

    def test_review_requires_finished_extraction(self):
        detail, _ = self.create()
        with self.assertRaises(DomainError):
            self.repo.review_job(
                detail["job"]["id"], detail["job"]["version"], "ACCEPTED",
                payload(), {"issues": []}, "premature",
            )

    def test_acceptance_recomputes_validation_instead_of_trusting_supplied_issues(self):
        detail = self.complete()
        candidate = deepcopy(detail["extraction"])
        candidate["members"][0]["nik_raw"] = "12?"
        with self.assertRaises(DomainError) as raised:
            self.repo.review_job(
                detail["job"]["id"], detail["job"]["version"], "ACCEPTED",
                candidate, {"issues": []}, "attempted validation bypass",
            )
        self.assertEqual(422, raised.exception.status)
        final = self.repo.get_job(detail["job"]["id"])
        self.assertEqual(detail, final)

    def test_not_reviewed_evidence_blocks_acceptance(self):
        detail = self.complete()
        candidate = deepcopy(detail["extraction"])
        next(entry for entry in candidate["field_evidence"] if entry["path"] == "household.address")["status"] = "NOT_REVIEWED"
        with self.assertRaises(DomainError) as raised:
            self.repo.review_job(
                detail["job"]["id"], detail["job"]["version"], "ACCEPTED",
                candidate, {"issues": []}, "unreviewed field",
            )
        self.assertEqual(422, raised.exception.status)

    def test_deleting_not_reviewed_evidence_cannot_bypass_acceptance(self):
        detail = self.complete()
        candidate = deepcopy(detail["extraction"])
        next(entry for entry in candidate["field_evidence"] if entry["path"] == "household.address")["status"] = "NOT_REVIEWED"
        draft = self.repo.review_job(
            detail["job"]["id"], detail["job"]["version"], "NEEDS_REVIEW", candidate, {"issues": []}, "pending field check",
        )
        candidate["field_evidence"] = [entry for entry in candidate["field_evidence"] if entry["path"] != "household.address"]
        with self.assertRaises(DomainError) as raised:
            self.repo.review_job(
                draft["job"]["id"], draft["job"]["version"], "ACCEPTED", candidate, {"issues": []}, "removed unresolved entry",
            )
        self.assertEqual(422, raised.exception.status)
        self.assertEqual(draft, self.repo.get_job(draft["job"]["id"]))

    def test_all_evidence_removed_blocks_acceptance_but_draft_can_be_saved(self):
        detail = self.complete()
        candidate = deepcopy(detail["extraction"])
        candidate["field_evidence"] = []
        with self.assertRaises(DomainError) as raised:
            self.repo.review_job(
                detail["job"]["id"], detail["job"]["version"], "ACCEPTED", candidate, {"issues": []}, "attempted evidence omission",
            )
        self.assertEqual(422, raised.exception.status)
        draft = self.repo.review_job(
            detail["job"]["id"], detail["job"]["version"], "NEEDS_REVIEW", candidate, {"issues": []}, "draft awaiting evidence",
        )
        self.assertEqual("NEEDS_REVIEW", draft["job"]["status"])
        self.assertTrue(any(issue["severity"] == "ERROR" for issue in draft["validation"]["issues"]))

    def test_duplicate_alias_evidence_blocks_acceptance(self):
        detail = self.complete()
        candidate = deepcopy(detail["extraction"])
        duplicate = deepcopy(next(entry for entry in candidate["field_evidence"] if entry["path"] == "members[0].nik_raw"))
        duplicate["path"] = "members.0.nik_raw"
        candidate["field_evidence"].append(duplicate)
        with self.assertRaises(DomainError) as raised:
            self.repo.review_job(
                detail["job"]["id"], detail["job"]["version"], "ACCEPTED", candidate, {"issues": []}, "duplicate aliases",
            )
        self.assertEqual(422, raised.exception.status)

    def test_wrong_evidence_row_cannot_approve_another_member(self):
        detail = self.complete()
        candidate = deepcopy(detail["extraction"])
        second = deepcopy(candidate["members"][0])
        second.update(row_number=2, nik_raw="2222222222222222")
        candidate["members"].append(second)
        for field, value in second.items():
            candidate["field_evidence"].append({
                "path": f"members[1].{field}", "status": "READABLE" if value is not None else "BLANK_ON_DOCUMENT",
                "section": "members", "row_number": 2,
            })
        next(entry for entry in candidate["field_evidence"] if entry["path"] == "members[0].nik_raw")["row_number"] = 2
        with self.assertRaises(DomainError) as raised:
            self.repo.review_job(
                detail["job"]["id"], detail["job"]["version"], "ACCEPTED", candidate, {"issues": []}, "mismatched evidence row",
            )
        self.assertEqual(422, raised.exception.status)

    def test_contradictory_evidence_status_cannot_hide_populated_value(self):
        detail = self.complete()
        for status in ("BLANK_ON_DOCUMENT", "UNREADABLE", "CROPPED", "NOT_PRESENT_IN_LAYOUT", "AMBIGUOUS"):
            candidate = deepcopy(detail["extraction"])
            candidate["household"]["address"] = "Synthetic invented address"
            next(entry for entry in candidate["field_evidence"] if entry["path"] == "household.address")["status"] = status
            with self.subTest(status=status), self.assertRaises(DomainError) as raised:
                self.repo.review_job(
                    detail["job"]["id"], detail["job"]["version"], "ACCEPTED", candidate, {"issues": []}, "contradictory evidence",
                )
            self.assertEqual(422, raised.exception.status)

    def test_unknown_layout_cannot_be_accepted_even_with_complete_evidence(self):
        detail = self.complete()
        candidate = deepcopy(detail["extraction"])
        candidate["layout_family"] = "unknown"
        with self.assertRaises(DomainError) as raised:
            self.repo.review_job(
                detail["job"]["id"], detail["job"]["version"], "ACCEPTED", candidate, {"issues": []}, "layout remains unknown",
            )
        self.assertEqual(422, raised.exception.status)

    def test_legacy_nonstandard_identifiers_cannot_be_accepted_without_review_comment(self):
        detail = self.complete()
        candidate = deepcopy(detail["extraction"])
        candidate["layout_family"] = "legacy"
        candidate["household"]["kk_number_raw"] = "00.00/0001"
        candidate["members"][0]["nik_raw"] = "000002"
        for comment in ("", " \n\t "):
            with self.subTest(comment=repr(comment)), self.assertRaises(DomainError) as raised:
                self.repo.review_job(
                    detail["job"]["id"], detail["job"]["version"], "ACCEPTED",
                    candidate, {"issues": []}, comment,
                )
            self.assertEqual(422, raised.exception.status)
            self.assertEqual(detail, self.repo.get_job(detail["job"]["id"]))

    def test_legacy_manual_acceptance_preserves_identifiers_and_audits_explanation(self):
        detail = self.complete()
        candidate = deepcopy(detail["extraction"])
        candidate["layout_family"] = "legacy"
        candidate["household"]["kk_number_raw"] = "00.00/0001"
        candidate["members"][0]["nik_raw"] = "000002"
        comment = "Checked synthetic legacy source; recorded identifiers exactly as printed."
        result = self.repo.review_job(
            detail["job"]["id"], detail["job"]["version"], "ACCEPTED",
            candidate, {"issues": []}, comment,
        )
        self.assertEqual("ACCEPTED", result["job"]["status"])
        self.assertEqual(candidate, result["extraction"])
        self.assertEqual(comment, result["review_history"][0]["comment"])
        identifier_issues = [issue for issue in result["validation"]["issues"]
                             if issue["code"] == "LEGACY_IDENTIFIER_REVIEW"]
        self.assertEqual(2, len(identifier_issues))
        self.assertTrue(all(issue["severity"] == "WARNING" for issue in identifier_issues))
        exported = self.repo.export_job(detail["job"]["id"])
        self.assertEqual(candidate, exported["review_history"][0]["extraction"])
        self.assertEqual(comment, exported["review_history"][0]["comment"])

    def test_legacy_draft_can_save_without_comment_and_remains_needs_review(self):
        detail = self.complete()
        candidate = deepcopy(detail["extraction"])
        candidate["layout_family"] = "legacy"
        candidate["household"]["kk_number_raw"] = "00.00/0001"
        candidate["members"][0]["nik_raw"] = "000002"
        result = self.repo.review_job(
            detail["job"]["id"], detail["job"]["version"], "NEEDS_REVIEW",
            candidate, {"issues": []}, "",
        )
        self.assertEqual("NEEDS_REVIEW", result["job"]["status"])
        self.assertEqual(candidate, result["extraction"])

    def test_review_comment_does_not_override_modern_identifier_error(self):
        detail = self.complete()
        candidate = deepcopy(detail["extraction"])
        candidate["members"][0]["nik_raw"] = "00.00/0001"
        with self.assertRaises(DomainError) as raised:
            self.repo.review_job(
                detail["job"]["id"], detail["job"]["version"], "ACCEPTED",
                candidate, {"issues": []}, "A comment cannot waive modern format validation.",
            )
        self.assertEqual(422, raised.exception.status)

    def test_incomplete_review_can_be_saved_and_keeps_validation_errors(self):
        detail = self.complete()
        candidate = deepcopy(detail["extraction"])
        candidate["members"][0]["nik_raw"] = "12?"
        result = self.repo.review_job(
            detail["job"]["id"], detail["job"]["version"], "NEEDS_REVIEW",
            candidate, {"issues": []}, "awaiting clearer source",
        )
        self.assertEqual("NEEDS_REVIEW", result["job"]["status"])
        self.assertTrue(any(issue["severity"] == "ERROR" for issue in result["validation"]["issues"]))
        self.assertEqual("12?", result["extraction"]["members"][0]["nik_raw"])

    def test_export_contains_source_version_and_full_review_audit(self):
        detail = self.complete()
        candidate = deepcopy(detail["extraction"])
        candidate["household"]["address"] = "Synthetic corrected address"
        next(entry for entry in candidate["field_evidence"] if entry["path"] == "household.address")["status"] = "READABLE"
        reviewed = self.repo.review_job(
            detail["job"]["id"], detail["job"]["version"], "ACCEPTED",
            candidate, {"issues": []}, "checked synthetic source",
        )
        exported = self.repo.export_job(detail["job"]["id"])
        self.assertEqual(detail["job"]["sha256"], exported["export"]["source_sha256"])
        self.assertEqual(reviewed["job"]["version"], exported["export"]["job_version"])
        self.assertFalse(exported["export"]["is_ai_extraction"])
        self.assertTrue(exported["export"]["human_accepted"])
        audit = exported["review_history"][0]
        self.assertEqual(detail["extraction"], audit["previous_extraction"])
        self.assertEqual(candidate, audit["extraction"])
        self.assertEqual(detail["job"]["version"], audit["from_version"])
        self.assertEqual(reviewed["job"]["version"], audit["to_version"])

    def test_search_treats_sql_wildcards_and_quotes_as_literal_text(self):
        unusual, _ = self.create(filename="synthetic_100%.png")
        self.create(filename="synthetic-other.png")
        self.assertEqual([unusual["job"]["id"]], [job["id"] for job in self.repo.list_jobs(query="100%")])
        self.assertEqual([], self.repo.list_jobs(query="' OR 1=1 --"))
        self.assertEqual(2, len(self.repo.list_jobs(status="QUEUED")))

    def test_optimistic_review_race_records_only_winning_change(self):
        detail = self.complete()
        barrier = threading.Barrier(2)

        def review(comment):
            candidate = deepcopy(detail["extraction"])
            candidate["household"]["address"] = comment
            next(entry for entry in candidate["field_evidence"] if entry["path"] == "household.address")["status"] = "READABLE"
            barrier.wait(timeout=10)
            try:
                return self.repo.review_job(
                    detail["job"]["id"], detail["job"]["version"], "ACCEPTED",
                    candidate, {"issues": []}, comment,
                )
            except DomainError as error:
                return error

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(review, ["reviewer-one", "reviewer-two"]))
        successes = [result for result in results if isinstance(result, dict)]
        failures = [result for result in results if isinstance(result, DomainError)]
        self.assertEqual(1, len(successes))
        self.assertEqual(1, len(failures))
        self.assertEqual(409, failures[0].status)
        final = self.repo.get_job(detail["job"]["id"])
        self.assertEqual("ACCEPTED", final["job"]["status"])
        self.assertEqual(detail["job"]["version"] + 1, final["job"]["version"])
        self.assertEqual(1, len(final["review_history"]))
        self.assertEqual(final["extraction"], successes[0]["extraction"])

    def test_sql_and_html_like_values_are_stored_as_literal_text(self):
        detail = self.complete()
        candidate = deepcopy(detail["extraction"])
        marker = "<script>alert('synthetic')</script>'; DROP TABLE jobs; --"
        candidate["household"]["address"] = marker
        reviewed = self.repo.review_job(
            detail["job"]["id"], detail["job"]["version"], "NEEDS_REVIEW",
            candidate, {"issues": []}, marker,
        )
        self.assertEqual(marker, reviewed["extraction"]["household"]["address"])
        self.assertEqual(1, len(self.repo.list_jobs()))
        self.assertEqual(1, len(reviewed["review_history"]))

    def test_case_scope_allows_same_raw_nik_in_different_records(self):
        one, _ = self.create(case_id="case-one")
        two, _ = self.create(case_id="case-two")
        for _ in range(2):
            claimed = self.repo.claim_job()
            self.assertTrue(self.repo.complete_job(
                claimed["id"], claimed["lease_token"], payload(), {"issues": []}
            ))
        self.assertEqual(
            self.repo.get_job(one["job"]["id"])["extraction"]["members"][0]["nik_raw"],
            self.repo.get_job(two["job"]["id"])["extraction"]["members"][0]["nik_raw"],
        )


if __name__ == "__main__":
    unittest.main()
