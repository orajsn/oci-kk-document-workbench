"""Worker integration tests with explicit synthetic adapters and durable sources."""

import hashlib
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from kkpilot.config import Config
from kkpilot.repository import Repository
from kkpilot.worker import WorkerPool
from test_backend_validation import payload, png


class Extractor:
    def __init__(self, outcome):
        self.outcome = outcome

    def extract(self, image_path, context):
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class RetryableError(Exception):
    code = "TEMPORARY_FAILURE"
    retryable = True


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = Config(data_dir=Path(self.temp.name), lease_seconds=1, retry_delay_seconds=0)
        self.config.image_dir.mkdir()
        self.repo = Repository(self.config.database_path)
        self.repo.initialize()
        self.addCleanup(self.temp.cleanup)

    def create(self):
        image = png()
        stored_name = uuid.uuid4().hex + ".png"
        (self.config.image_dir / stored_name).write_bytes(image)
        return self.repo.create_job(case_id="synthetic", filename="test.png", mode="demo", image_relpath=stored_name,
                                    sha256=hashlib.sha256(image).hexdigest(), idempotency_key=uuid.uuid4().hex, request_hash="x", max_attempts=3)[0]

    def pool(self, outcome):
        return WorkerPool(self.repo, self.config, lambda mode, config: Extractor(outcome))

    def test_valid_extraction_always_requires_review(self):
        job = self.create()["job"]
        pool = self.pool(payload())
        self.assertTrue(pool.process_once())
        self.assertFalse(pool.process_once())
        self.assertEqual("NEEDS_REVIEW", self.repo.get_job(job["id"])["job"]["status"])

    def test_invalid_model_json_is_failed_and_never_persisted_as_extraction(self):
        for value in ("{model text}", {"not": "the schema"}, None):
            job = self.create()["job"]
            self.pool(value).process_once()
            detail = self.repo.get_job(job["id"])
            self.assertEqual("FAILED", detail["job"]["status"])
            self.assertEqual("INVALID_EXTRACTION", detail["job"]["last_error"])
            self.assertIsNone(detail["extraction"])

    def test_retryable_adapter_failure_is_bounded(self):
        job = self.create()["job"]
        pool = self.pool(RetryableError("private details must not escape"))
        for _ in range(3):
            self.assertTrue(pool.process_once())
        self.assertFalse(pool.process_once())
        detail = self.repo.get_job(job["id"])
        self.assertEqual("FAILED", detail["job"]["status"])
        self.assertEqual(3, detail["job"]["attempts"])
        self.assertEqual("TEMPORARY_FAILURE", detail["job"]["last_error"])

    def test_arbitrary_exception_text_never_becomes_public_error(self):
        job = self.create()["job"]
        self.pool(RuntimeError("PRIVATE NAME private/source/path secret")).process_once()
        detail = self.repo.get_job(job["id"])
        self.assertEqual("EXTRACTION_FAILED", detail["job"]["last_error"])
        self.assertNotIn("PRIVATE", str(detail))

    def test_changed_source_fails_before_adapter_is_called(self):
        job = self.create()["job"]
        next(self.config.image_dir.iterdir()).write_bytes(b"changed")
        called = []
        pool = WorkerPool(self.repo, self.config, lambda *args: called.append(args))
        pool.process_once()
        self.assertEqual([], called)
        self.assertEqual("SOURCE_CHANGED", self.repo.get_job(job["id"])["job"]["last_error"])

    def test_oversized_changed_source_is_rejected_without_reading_it(self):
        job = self.create()["job"]
        source = next(self.config.image_dir.iterdir())
        with source.open("r+b") as stream:
            stream.truncate(self.config.max_upload_bytes + 1)
        called = []
        pool = WorkerPool(self.repo, self.config, lambda *args: called.append(args))
        with patch.object(Path, "read_bytes", side_effect=AssertionError("Unbounded read forbidden")):
            pool.process_once()
        self.assertEqual([], called)
        self.assertEqual("SOURCE_CHANGED", self.repo.get_job(job["id"])["job"]["last_error"])

    def test_explicit_factory_receives_only_local_configuration(self):
        job = self.create()["job"]
        observed = []

        class CaptureExtractor:
            def extract(self, image_path, context):
                observed.append((image_path, context))
                return payload()

        def factory(mode, config):
            observed.append((mode, config))
            return CaptureExtractor()

        WorkerPool(self.repo, self.config, factory).process_once()
        self.assertEqual(("demo", {"reference_dir": None}), observed[0])
        self.assertEqual(job["id"], observed[1][1]["job_id"])
        self.assertEqual(job["sha256"], observed[1][1]["sha256"])
        self.assertTrue(observed[1][0].is_relative_to(self.config.image_dir))

    def test_worker_pool_bounds_parallel_extraction(self):
        jobs = [self.create()["job"]["id"] for _ in range(6)]
        lock = threading.Lock()
        released = threading.Event()
        two_started = threading.Event()
        finished = threading.Event()
        counts = {"active": 0, "maximum": 0, "finished": 0}

        class BlockingExtractor:
            def extract(self, image_path, context):
                with lock:
                    counts["active"] += 1
                    counts["maximum"] = max(counts["maximum"], counts["active"])
                    if counts["active"] == 2:
                        two_started.set()
                released.wait(3)
                with lock:
                    counts["active"] -= 1
                    counts["finished"] += 1
                    if counts["finished"] == 6:
                        finished.set()
                return payload()

        pool = WorkerPool(self.repo, self.config, lambda *args: BlockingExtractor())
        pool.start()
        try:
            self.assertTrue(two_started.wait(3))
            self.assertEqual(2, counts["maximum"])
            released.set()
            self.assertTrue(finished.wait(3))
        finally:
            released.set()
            pool.stop()
        self.assertEqual(2, counts["maximum"])
        self.assertTrue(all(self.repo.get_job(job_id)["job"]["status"] == "NEEDS_REVIEW" for job_id in jobs))


if __name__ == "__main__":
    unittest.main()
