"""Bounded cloud-operator orchestration with injected clients and no SDK imports.

The CLI is responsible for --execute and full scope/configuration validation. This
layer repeats the explicit approval guards, keeps remote I/O outside repository
transactions, and acknowledges a queue delivery only after a durable terminal
state. Importing or constructing it never connects to a service.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import uuid

from .contracts import CloudError
from ..adapters import get_extractor
from ..adapters.errors import AdapterError
from ..repository import safe_refinement_metadata
from ..telemetry import DeliveryTiming, summarize_deliveries
from ..validation import ValidationError, assert_valid_payload, inspect_image, validate_payload


ACK = "APPROVED_KK_CLOUD_OPERATOR_PILOT"
_HASH = re.compile(r"[0-9a-f]{64}")
_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
_OPAQUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_NAMESPACE = uuid.UUID("bec8c74b-9d91-4cb0-a14d-b28cc90e7025")
_USAGE_KEYS = frozenset({
    "input_tokens", "output_tokens", "prompt_tokens", "completion_tokens", "total_tokens",
    "cached_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "reasoning_tokens",
    "billed_units", "tokens", "prompt_tokens_details", "completion_tokens_details",
    "audio_tokens", "image_tokens", "accepted_prediction_tokens", "rejected_prediction_tokens",
})


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _safe_code(exc: Exception, fallback: str = "CLOUD_OPERATION_FAILED") -> str:
    code = getattr(exc, "code", None)
    return code if isinstance(code, str) and _CODE.fullmatch(code) else fallback


def _is_uuid(value: object) -> bool:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F-]{32,36}", value):
        return False
    try:
        uuid.UUID(value)
        return True
    except ValueError:
        return False


def _bounded_text(value: object, maximum: int, code: str) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise CloudError(code)
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise CloudError(code) from None
    return value


def _numeric_tree(value: object, depth: int = 0):
    """Keep usage counters, never provider text or arbitrary nested payloads."""
    if type(value) in (int, float) and math.isfinite(value) and value >= 0:
        return value
    if depth < 3 and isinstance(value, dict):
        result = {}
        for key, child in list(value.items())[:32]:
            if key in _USAGE_KEYS:
                parsed = _numeric_tree(child, depth + 1)
                if parsed is not None:
                    result[key] = parsed
        return result
    return None


class Workflow:
    """One approved operator scope; each method performs a bounded operation."""

    def __init__(self, cfg: dict, repository, objects, queue, extractor_factory=None):
        self.cfg = copy.deepcopy(cfg)
        for key in ("enabled", "data_processing_authorized", "spending_authorized"):
            if self.cfg.get(key) is not True:
                raise CloudError("CLOUD_APPROVAL_REQUIRED")
        if self.cfg.get("ack") != ACK:
            raise CloudError("CLOUD_ACK_REQUIRED")
        _bounded_text(self.cfg.get("authorization_reference"), 500, "CLOUD_APPROVAL_SCOPE_REQUIRED")
        for key in ("scope_id", "config_sha256", "object_storage_namespace", "object_bucket"):
            _bounded_text(self.cfg.get(key), 256, "CLOUD_CONFIGURATION_INVALID")
        if self.cfg.get("region") != "ap-batam-1":
            raise CloudError("BATAM_DATA_REGION_REQUIRED")
        for key in ("max_requests", "max_attempts", "max_upload_bytes", "lease_seconds", "visibility_seconds", "backoff_seconds"):
            if type(self.cfg.get(key)) is not int or self.cfg[key] < 1:
                raise CloudError("CLOUD_CONFIGURATION_INVALID")
        if self.cfg["max_requests"] > 10000 or self.cfg["max_attempts"] > 10:
            raise CloudError("CLOUD_CONFIGURATION_INVALID")
        if not 1024 <= self.cfg["max_upload_bytes"] <= 50 * 1024 * 1024:
            raise CloudError("CLOUD_CONFIGURATION_INVALID")
        if self.cfg["lease_seconds"] < 180 or self.cfg["visibility_seconds"] < self.cfg["lease_seconds"] + 30:
            raise CloudError("CLOUD_LEASE_CONFIGURATION_INVALID")
        if self.cfg["backoff_seconds"] > self.cfg["visibility_seconds"]:
            raise CloudError("CLOUD_LEASE_CONFIGURATION_INVALID")
        adapter = self.cfg.get("oci_adapter")
        if not isinstance(adapter, dict):
            raise CloudError("CLOUD_CONFIGURATION_INVALID")
        hashes = adapter.get("approved_image_sha256s")
        if not isinstance(hashes, list) or not hashes or any(not isinstance(x, str) or not _HASH.fullmatch(x) for x in hashes):
            raise CloudError("APPROVED_IMAGE_HASHES_REQUIRED")
        self._approved_hashes = frozenset(hashes)
        self.repository, self.objects, self.queue = repository, objects, queue
        self.extractor_factory = extractor_factory or get_extractor
        self.owner = "operator-" + uuid.uuid4().hex

    def _inspect(self, data: bytes) -> tuple[str, str]:
        if not isinstance(data, bytes) or not data or len(data) > self.cfg["max_upload_bytes"]:
            raise CloudError("IMAGE_SIZE_INVALID")
        maximum_pixels = self.cfg.get("max_pixels", 40_000_000)
        if type(maximum_pixels) is not int or not 1 <= maximum_pixels <= 100_000_000:
            raise CloudError("CLOUD_CONFIGURATION_INVALID")
        try:
            mime, extension, _, _ = inspect_image(data, max_pixels=maximum_pixels)
        except ValidationError as exc:
            raise CloudError(_safe_code(exc, "IMAGE_INVALID")) from None
        return mime, extension

    def submission_status(self, case_id: str, idempotency_key: str) -> str | None:
        """Find a committed submission in this processing scope after a lost ACK."""
        for value, code in ((case_id, "CASE_ID_INVALID"), (idempotency_key, "IDEMPOTENCY_KEY_INVALID")):
            if not isinstance(value, str) or not _OPAQUE.fullmatch(value):
                raise CloudError(code)
        existing = self.repository.find_submission(case_id, idempotency_key)
        if existing and existing["scope_id"] == self.cfg["scope_id"]:
            return existing["job_id"]
        return None

    def enqueue(self, image: Path, case_id: str, idempotency_key: str) -> dict:
        """Upload one approved immutable byte snapshot, then commit job/outbox."""
        image = Path(image)
        try:
            with image.open("rb") as source:
                content = source.read(self.cfg["max_upload_bytes"] + 1)
        except OSError:
            raise CloudError("SOURCE_READ_FAILED") from None
        return self.enqueue_bytes(content, image.name, case_id, idempotency_key)

    def enqueue_bytes(self, content: bytes, original_filename: str, case_id: str,
                      idempotency_key: str) -> dict:
        """Submit an approved byte snapshot without writing a web upload to disk."""
        if not isinstance(case_id, str) or not _OPAQUE.fullmatch(case_id):
            raise CloudError("CASE_ID_INVALID")
        if not isinstance(idempotency_key, str) or not _OPAQUE.fullmatch(idempotency_key):
            raise CloudError("IDEMPOTENCY_KEY_INVALID")
        filename = _bounded_text(original_filename, 512, "FILENAME_INVALID")
        if not isinstance(content, bytes) or not content or len(content) > self.cfg["max_upload_bytes"]:
            raise CloudError("IMAGE_SIZE_INVALID")
        sha256 = hashlib.sha256(content).hexdigest()
        if sha256 not in self._approved_hashes:
            raise CloudError("SOURCE_NOT_APPROVED")
        mime, extension = self._inspect(content)
        adapter = self.cfg["oci_adapter"]
        fingerprint = hashlib.sha256(_canonical({
            "scope_id": self.cfg["scope_id"], "config_sha256": self.cfg["config_sha256"],
            "case_id": case_id, "idempotency_key": idempotency_key, "sha256": sha256,
            "filename": filename, "model_id": adapter["expected_model_id"],
            "routing_profile_id": adapter["routing_profile_id"], "schema_version": "1.0",
        }).encode("utf-8")).hexdigest()
        existing = self.repository.find_submission(case_id, idempotency_key)
        if existing is not None:
            if existing.get("request_fingerprint") != fingerprint:
                raise CloudError("IDEMPOTENCY_CONFLICT")
            return {"outcome": "already_enqueued", "job_id": existing["job_id"],
                    "status": existing["status"], "created": False}
        seed = _canonical([self.cfg["scope_id"], case_id, idempotency_key])
        job_id = str(uuid.uuid5(_NAMESPACE, seed))
        object_name = f"sources/{job_id}/{sha256}{extension}"
        # The original path is never reopened after this approved snapshot.
        stored = self.objects.put_immutable(object_name, content, sha256, mime)
        version_id = stored.get("version_id") if isinstance(stored, dict) else None
        if not isinstance(stored, dict) or (version_id is not None and (not isinstance(version_id, str) or len(version_id) > 256)):
            raise CloudError("OBJECT_METADATA_INVALID")
        submission = {
            "job_id": job_id, "document_id": str(uuid.uuid5(uuid.UUID(job_id), "document")),
            "outbox_id": str(uuid.uuid5(uuid.UUID(job_id), "initial-outbox")),
            "case_id": case_id, "idempotency_key": idempotency_key,
            "request_fingerprint": fingerprint, "sha256": sha256, "filename": filename,
            "mime_type": mime, "byte_length": len(content), "object_name": object_name,
            "object_version_id": version_id, "object_namespace": self.cfg["object_storage_namespace"],
            "object_bucket": self.cfg["object_bucket"], "data_region": self.cfg["region"],
            "scope_id": self.cfg["scope_id"], "model_id": adapter["expected_model_id"],
            "routing_profile_id": adapter["routing_profile_id"], "schema_version": "1.0",
            "max_attempts": self.cfg["max_attempts"],
        }
        saved = self.repository.enqueue(submission)
        return {"outcome": "enqueued" if saved.get("created", True) else "already_enqueued",
                "job_id": saved.get("job_id", job_id), "status": saved.get("status", "QUEUED"),
                "created": saved.get("created", True)}

    def _message_payload(self, content: object) -> dict:
        if not isinstance(content, dict) or set(content) != {"schema_version", "type", "job_id", "scope_id"}:
            raise CloudError("QUEUE_MESSAGE_INVALID")
        if content["schema_version"] != "1.0" or content["type"] != "KK_JOB" or not _is_uuid(content["job_id"]):
            raise CloudError("QUEUE_MESSAGE_INVALID")
        if content["scope_id"] != self.cfg["scope_id"]:
            raise CloudError("QUEUE_SCOPE_MISMATCH")
        return content

    def publish_once(self) -> dict:
        claimed = self.repository.claim_outbox(self.cfg["scope_id"], self.owner, self.cfg["lease_seconds"])
        if claimed is None:
            return {"outcome": "empty"}
        try:
            payload = self._message_payload(claimed["payload"])
            if payload["job_id"] != claimed.get("job_id"):
                raise CloudError("OUTBOX_JOB_MISMATCH")
            message_id = self.queue.publish(payload)
            _bounded_text(message_id, 256, "QUEUE_PUBLISH_RESPONSE_INVALID")
        except Exception as exc:
            code = _safe_code(exc, "QUEUE_PUBLISH_FAILED")
            released = self.repository.release_outbox(claimed["outbox_id"], claimed["lease_token"],
                                                       self.cfg["backoff_seconds"], code)
            return {"outcome": "publish_failed", "code": code, "released": released}
        # A publish-before-mark crash can duplicate a notification; jobs are fenced.
        marked = self.repository.mark_published(claimed["outbox_id"], claimed["lease_token"], message_id)
        return {"outcome": "published" if marked else "publish_lease_lost", "marked": marked}

    def _defer(self, receipt: str, seconds: int) -> bool:
        try:
            self.queue.defer(receipt, seconds)
            return True
        except Exception:
            # The original visibility deadline remains; retain the DB job/outbox.
            return False

    def _ack(self, receipt: str, outcome: str, **extra) -> dict:
        try:
            self.queue.acknowledge(receipt)
            return {"outcome": outcome, "acknowledged": True, **extra}
        except Exception:
            # Completion is durable already. Redelivery must discover terminal state.
            return {"outcome": "ack_failed", "durable_outcome": outcome,
                    "acknowledged": False, "code": "QUEUE_ACK_FAILED", **extra}

    def _claim_source(self, claim: dict, job_id: str) -> tuple[str, str | None]:
        if claim.get("job_id") != job_id or claim.get("object_namespace") != self.cfg["object_storage_namespace"] or claim.get("object_bucket") != self.cfg["object_bucket"]:
            raise CloudError("CLAIM_SOURCE_SCOPE_MISMATCH")
        if claim.get("scope_id", self.cfg["scope_id"]) != self.cfg["scope_id"]:
            raise CloudError("CLAIM_SOURCE_SCOPE_MISMATCH")
        sha256 = claim.get("sha256")
        if sha256 not in self._approved_hashes:
            raise CloudError("SOURCE_NOT_APPROVED")
        name = claim.get("object_name")
        allowed = {f"sources/{job_id}/{sha256}.png", f"sources/{job_id}/{sha256}.jpg"}
        if name not in allowed:
            raise CloudError("CLAIM_OBJECT_KEY_INVALID")
        version_id = claim.get("object_version_id")
        if version_id is not None and (not isinstance(version_id, str) or len(version_id) > 256):
            raise CloudError("OBJECT_METADATA_INVALID")
        for key in ("version", "attempt_count", "max_attempts", "byte_length"):
            if type(claim.get(key)) is not int or claim[key] < 1:
                raise CloudError("CLAIM_METADATA_INVALID")
        _bounded_text(claim.get("lease_token"), 36, "CLAIM_METADATA_INVALID")
        _bounded_text(claim.get("case_id"), 128, "CLAIM_METADATA_INVALID")
        return name, version_id

    def _metadata(self, raw: object) -> dict:
        if not isinstance(raw, dict):
            raw = {}
        adapter = self.cfg["oci_adapter"]
        served = raw.get("served_region")
        if served is not None and served not in adapter["approved_serving_regions"]:
            raise CloudError("UNAPPROVED_SERVED_REGION")
        result = {"mode": "oci", "entry_region": self.cfg["region"],
                  "model": adapter["expected_model_id"], "served_region": served,
                  "live_test": raw.get("live_test") is True}
        for key in ("prompt_sha256", "schema_sha256", "sent_image_sha256"):
            if isinstance(raw.get(key), str) and _HASH.fullmatch(raw[key]):
                result[key] = raw[key]
        for key in ("prompt_version", "schema_version"):
            value = raw.get(key)
            if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,50}", value):
                result[key] = value
        request_id = raw.get("request_id")
        if isinstance(request_id, str) and re.fullmatch(r"[A-Za-z0-9:/._-]{1,512}", request_id):
            result["request_id"] = request_id
        elapsed = raw.get("elapsed_seconds")
        if type(elapsed) in (int, float) and math.isfinite(elapsed) and elapsed >= 0:
            result["elapsed_seconds"] = elapsed
        if adapter.get("pipeline") == "gemini_image_only":
            if (served != "us-chicago-1" or adapter.get("expected_model_id") != "google.gemini-2.5-pro"
                    or adapter.get("external_processor") != "Google"
                    or adapter.get("external_processing_scope") != "Google Americas"):
                raise CloudError("GEMINI_PROCESSING_METADATA_MISMATCH")
            local = adapter.get("local_ocr")
            ocr_used = isinstance(local, dict) and local.get("enabled") is True and raw.get("ocr_used") is True
            result.update(pipeline="gemini_image_only", external_processor="Google",
                          external_processing_scope="Google Americas", ocr_used=ocr_used)
            result.update(safe_refinement_metadata(raw))
            for key in ("preparation_seconds", "model_call_seconds"):
                value = raw.get(key)
                if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 3600:
                    result[key] = value
        if adapter.get("pipeline") in ("document_genai_hybrid", "document_meta_multiview"):
            if raw.get("ocr_region") == "ap-batam-1":
                result["ocr_region"] = "ap-batam-1"
            if raw.get("wire_version") == "ocr-hybrid-short-1.0":
                result["wire_version"] = "ocr-hybrid-short-1.0"
            for key in ("preparation_seconds", "ocr_seconds", "model_call_seconds"):
                value = raw.get(key)
                if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 3600:
                    result[key] = value
            counts = raw.get("ocr_counts")
            if isinstance(counts, dict):
                result["ocr_counts"] = {k: counts[k] for k in ("pages", "lines", "words", "tables")
                                        if type(counts.get(k)) is int and 0 <= counts[k] <= 1000000}
        if adapter.get("pipeline") == "document_meta_multiview":
            if isinstance(raw.get("source_sha256"), str) and _HASH.fullmatch(raw["source_sha256"]):
                result["source_sha256"] = raw["source_sha256"]
            views = raw.get("views")
            if isinstance(views, list) and 1 <= len(views) <= 3:
                clean_views = []
                for view in views:
                    if not isinstance(view, dict) or view.get("role") not in ("original", "upper", "lower") or not isinstance(view.get("sha256"), str) or not _HASH.fullmatch(view["sha256"]):
                        break
                    clean = {"role": view["role"], "sha256": view["sha256"]}
                    for k, length in (("dimensions", 2), ("crop", 4)):
                        value = view.get(k)
                        if isinstance(value, list) and len(value) == length and all(type(x) is int and 0 <= x <= 100000 for x in value): clean[k] = value
                    if type(view.get("bytes")) is int and 0 < view["bytes"] <= 5242880: clean["bytes"] = view["bytes"]
                    clean_views.append(clean)
                if len(clean_views) == len(views):
                    result["views"] = clean_views; result["view_count"] = len(clean_views)
            audit = raw.get("normalizer_audit")
            if isinstance(audit, dict):
                clean = {k: audit[k] for k in ("canonical_sha256_before", "canonical_sha256_after") if isinstance(audit.get(k), str) and _HASH.fullmatch(audit[k])}
                changes = audit.get("missingness_changes")
                if isinstance(changes, list):
                    clean["missingness_changes"] = [{"path": x["path"], "raw_kind": x["raw_kind"]} for x in changes if isinstance(x, dict) and isinstance(x.get("path"), str) and re.fullmatch(r"(?:household\.[a-z_]+|members\[[0-9]+\]\.[a-z_]+)", x["path"]) and x.get("raw_kind") in ("EMPTY_STRING", "WHITESPACE_STRING")]
                result["normalizer_audit"] = clean
        preprocessing = raw.get("preprocessing")
        if isinstance(preprocessing, dict):
            clean = {}
            for key in ("original_size", "oriented_size", "sent_size"):
                value = preprocessing.get(key)
                if isinstance(value, (list, tuple)) and len(value) == 2 and all(type(x) is int and x > 0 for x in value):
                    clean[key] = list(value)
            for key in ("crop_applied", "automatic_localization"):
                if type(preprocessing.get(key)) is bool:
                    clean[key] = preprocessing[key]
            for key in ("exif_orientation", "rotation_degrees"):
                if type(preprocessing.get(key)) is int and 0 <= preprocessing[key] <= 360:
                    clean[key] = preprocessing[key]
            version = preprocessing.get("preprocessing_version")
            if isinstance(version, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,50}", version):
                clean["preprocessing_version"] = version
            result["preprocessing"] = clean
        response = raw.get("response")
        if isinstance(response, dict):
            clean = {}
            reason = response.get("finish_reason")
            if isinstance(reason, str) and re.fullmatch(r"[A-Za-z0-9_]{1,64}", reason):
                clean["finish_reason"] = reason
            usage = _numeric_tree(response.get("usage"))
            if usage is not None:
                clean["usage"] = usage
            result["response"] = clean
        return result

    def _failure(self, claim: dict, receipt: str, exc: Exception) -> dict:
        code = _safe_code(exc, "CLOUD_WORKER_FAILED")
        retryable = (getattr(exc, "retryable", False) is True) if isinstance(exc, (CloudError, AdapterError, ValidationError)) else True
        exhausted = claim.get("attempt_count", 0) >= claim.get("max_attempts", self.cfg["max_attempts"])
        try:
            recorded = self.repository.fail_job(claim, code, retryable, self.cfg["backoff_seconds"])
        except Exception:
            return {"outcome": "failure_unrecorded", "code": "DATABASE_FAILURE",
                    "acknowledged": False, "deferred": self._defer(receipt, self.cfg["visibility_seconds"])}
        if not recorded:
            return {"outcome": "lease_lost", "code": code, "acknowledged": False,
                    "deferred": self._defer(receipt, self.cfg["visibility_seconds"])}
        if not retryable or exhausted:
            return self._ack(receipt, "failed", code=code)
        return {"outcome": "retry_scheduled", "code": code, "acknowledged": False,
                "deferred": self._defer(receipt, self.cfg["backoff_seconds"])}

    def worker_once(self) -> dict:
        timing = DeliveryTiming()
        result = self._worker_once(timing)
        result["workflow_timing"] = timing.snapshot()
        return result

    def _worker_once(self, timing: DeliveryTiming) -> dict:
        message = timing.call("queue_receive_seconds", self.queue.receive, self.cfg["visibility_seconds"])
        if message is None:
            return {"outcome": "empty", "acknowledged": False}
        receipt = message.get("receipt") if isinstance(message, dict) else None
        _bounded_text(receipt, 8192, "QUEUE_RECEIPT_INVALID")
        try:
            payload = self._message_payload(message.get("content"))
        except CloudError as exc:
            return {"outcome": "rejected", "code": exc.code, "acknowledged": False,
                    "deferred": self._defer(receipt, self.cfg["visibility_seconds"])}
        claim = timing.call("claim_seconds", self.repository.claim_job, payload["job_id"], self.cfg["scope_id"], self.owner, self.cfg["lease_seconds"])
        outcome = claim.get("outcome")
        if outcome == "terminal":
            if claim.get("code") == "PRIOR_OUTCOME_UNKNOWN":
                return timing.call("ack_seconds", self._ack, receipt, "failed", code="PRIOR_OUTCOME_UNKNOWN")
            return timing.call("ack_seconds", self._ack, receipt, "duplicate_terminal")
        if outcome != "claimed":
            allowed = {"busy", "not_ready", "budget_exhausted", "not_found", "scope_mismatch"}
            return {"outcome": outcome if outcome in allowed else "claim_rejected",
                    "acknowledged": False, "deferred": self._defer(receipt, self.cfg["visibility_seconds"])}
        try:
            key, version_id = self._claim_source(claim, payload["job_id"])
            content = timing.call("source_read_seconds", self.objects.read_bounded, key, version_id, self.cfg["max_upload_bytes"])
            _, extension = timing.call("source_check_seconds", self._inspect, content)
            if hashlib.sha256(content).hexdigest() != claim["sha256"] or len(content) != claim["byte_length"]:
                raise CloudError("SOURCE_HASH_MISMATCH")
            if key != f"sources/{payload['job_id']}/{claim['sha256']}{extension}":
                raise CloudError("SOURCE_TYPE_MISMATCH")
            with tempfile.TemporaryDirectory(prefix="kk-cloud-", dir=self.cfg.get("private_temp_dir")) as folder:
                os.chmod(folder, 0o700)
                snapshot = Path(folder) / ("source" + extension)
                fd = os.open(snapshot, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(content)
                extractor = timing.call("extractor_setup_seconds", self.extractor_factory, "oci", self.cfg["oci_adapter"])
                extracted = timing.call("extraction_seconds", extractor.extract, snapshot, {"job_id": claim["job_id"], "case_id": claim["case_id"],
                    "sha256": claim["sha256"], "attempt_id": claim["lease_token"]})
                assert_valid_payload(extracted)
                validation = {"issues": timing.call("validation_seconds", validate_payload, extracted)}
                metadata = self._metadata(copy.deepcopy(getattr(extractor, "last_metadata", {})))
                # This immutable snapshot cannot include its own future commit or ACK time.
                metadata["workflow_timing"] = timing.snapshot("before_result_commit")
                committed = timing.call("result_commit_seconds", self.repository.complete_job, claim, extracted, validation, metadata)
        except Exception as exc:
            return timing.call("failure_handling_seconds", self._failure, claim, receipt, exc)
        if not committed:
            return {"outcome": "lease_lost", "acknowledged": False,
                    "deferred": self._defer(receipt, self.cfg["visibility_seconds"])}
        return timing.call("ack_seconds", self._ack, receipt, "needs_review", status="NEEDS_REVIEW")

    def run(self, max_jobs: int) -> dict:
        """Process at most max_jobs deliveries; no background daemon or busy loop."""
        if type(max_jobs) is not int or not 1 <= max_jobs <= self.cfg["max_requests"]:
            raise CloudError("WORKER_RUN_LIMIT_INVALID")
        counts = {}
        timings = []
        handled = 0
        for _ in range(max_jobs):
            result = self.worker_once()
            outcome = result["outcome"]
            timings.append((outcome, result["workflow_timing"]))
            counts[outcome] = counts.get(outcome, 0) + 1
            if outcome == "empty":
                break
            handled += 1
            if outcome in {"budget_exhausted", "failure_unrecorded", "ack_failed"}:
                break
        return {"outcome": "run_finished", "deliveries_handled": handled, "counts": counts,
                "timing_summary": summarize_deliveries(timings)}
