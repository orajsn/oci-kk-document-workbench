"""A bounded local worker pool; only explicit demo/reference adapters are loaded."""

from __future__ import annotations

import hashlib
import logging
import re
import threading
from pathlib import Path
from typing import Callable

from .config import Config
from .repository import Repository
from .validation import PayloadError, validate_payload


LOGGER = logging.getLogger("kkpilot.worker")


def _factory(mode: str, config: dict):
    # Import lazily so starting the local server never imports OCI or its credentials.
    from .adapters import get_extractor
    return get_extractor(mode, config)


class WorkerPool:
    def __init__(self, repository: Repository, config: Config, extractor_factory: Callable | None = None):
        self.repository, self.config = repository, config
        self.extractor_factory = extractor_factory or _factory
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        if self._threads:
            return
        self._stop.clear()
        for number in range(self.config.workers):
            thread = threading.Thread(target=self._run, name=f"kkpilot-worker-{number + 1}", daemon=True)
            self._threads.append(thread)
            thread.start()

    def notify(self) -> None:
        self._wake.set()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        self._wake.set()
        for thread in self._threads:
            thread.join(timeout / max(len(self._threads), 1))
        self._threads = [thread for thread in self._threads if thread.is_alive()]

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                worked = self.process_once()
            except Exception:
                # Never echo exception strings, extracted text, source paths or case IDs.
                LOGGER.error("Worker operation failed; durable queue will recover.")
                worked = False
            if not worked:
                self._wake.wait(0.25)
                self._wake.clear()

    def process_once(self) -> bool:
        job = self.repository.claim_job(self.config.lease_seconds)
        if job is None:
            return False
        heartbeat_stop = threading.Event()
        lease_lost = threading.Event()

        def keep_lease() -> None:
            interval = max(0.03, self.config.lease_seconds / 3)
            while not heartbeat_stop.wait(interval):
                if self._stop.is_set():
                    return
                try:
                    if not self.repository.heartbeat(job["id"], job["lease_token"], self.config.lease_seconds):
                        lease_lost.set()
                        return
                except Exception:
                    lease_lost.set()
                    return

        heartbeat = threading.Thread(target=keep_lease, name="kkpilot-lease", daemon=True)
        heartbeat.start()
        try:
            if job["mode"] not in ("demo", "reference", "oci") or (job["mode"] == "oci" and not self.config.live_oci_enabled):
                self.repository.fail_job(job["id"], job["lease_token"], "MODE_DISABLED", retryable=False)
                return True
            if job["mode"] == "oci" and not self.config.approved_image(job["sha256"]):
                self.repository.fail_job(job["id"], job["lease_token"], "SOURCE_NOT_APPROVED", retryable=False)
                return True
            image_path = (self.config.image_dir / job["image_relpath"]).resolve()
            if not image_path.is_relative_to(self.config.image_dir.resolve()) or not image_path.is_file():
                self.repository.fail_job(job["id"], job["lease_token"], "SOURCE_UNAVAILABLE", retryable=False)
                return True
            if image_path.stat().st_size > self.config.max_upload_bytes:
                self.repository.fail_job(job["id"], job["lease_token"], "SOURCE_CHANGED", retryable=False)
                return True
            with image_path.open("rb") as source:
                image_bytes = source.read(self.config.max_upload_bytes + 1)
            if len(image_bytes) > self.config.max_upload_bytes or hashlib.sha256(image_bytes).hexdigest() != job["sha256"]:
                self.repository.fail_job(job["id"], job["lease_token"], "SOURCE_CHANGED", retryable=False)
                return True
            extractor = self.extractor_factory(job["mode"], self.config.adapter_config(job["mode"]))
            payload = extractor.extract(image_path, {
                "job_id": job["id"], "case_id": job["case_id"], "sha256": job["sha256"],
                "attempt_id": job["lease_token"], "filename": job["filename"],
            })
            issues = validate_payload(payload)
            if not lease_lost.is_set():
                self.repository.complete_job(job["id"], job["lease_token"], payload, {"issues": issues},
                                             processing_metadata=getattr(extractor, "last_metadata", {}))
        except PayloadError:
            self.repository.fail_job(job["id"], job["lease_token"], "INVALID_EXTRACTION", retryable=False)
        except Exception as error:
            code = getattr(error, "code", "EXTRACTION_FAILED")
            if not isinstance(code, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", code):
                code = "EXTRACTION_FAILED"
            retryable = getattr(error, "retryable", False) is True
            delay = self.config.retry_delay_seconds * (2 ** (job["attempts"] - 1))
            self.repository.fail_job(job["id"], job["lease_token"], code, retryable=retryable, retry_delay=delay)
        finally:
            heartbeat_stop.set()
            heartbeat.join(timeout=1)
        return True
