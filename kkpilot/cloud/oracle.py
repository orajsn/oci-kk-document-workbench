"""Oracle repository for the guarded operator workflow.

Each public operation owns one short database session/transaction. No object,
queue or model client is called here. Importing/constructing this class does not
import the optional driver, read credentials or open a connection.
"""
from __future__ import annotations

from contextlib import contextmanager
import copy
import json
import re
import uuid

from .contracts import CloudError
from .shared_writer import SCHEMA, canonical, insert, write_snapshot
from ..validation import assert_valid_payload, validate_payload


def _row(cursor) -> dict | None:
    value = cursor.fetchone()
    if value is None:
        return None
    if isinstance(value, dict):
        return {str(key).lower(): item for key, item in value.items()}
    return {description[0].lower(): item for description, item in zip(cursor.description, value)}


def _json_value(value) -> dict:
    # Oracle 26ai / newer python-oracledb can materialize JSON-constrained
    # columns as native dictionaries rather than CLOBs or JSON strings.
    if isinstance(value, dict):
        return copy.deepcopy(value)
    if hasattr(value, "read"):
        value = value.read()
    result = json.loads(value)
    if not isinstance(result, dict):
        raise CloudError("STORED_OUTBOX_INVALID")
    return result


def _unique_violation(exc: Exception) -> bool:
    error = exc.args[0] if exc.args else exc
    return getattr(error, "code", None) == 1


def _seconds(value: int, *, maximum: int = 3600, zero: bool = False) -> int:
    if type(value) is not int or not (0 if zero else 1) <= value <= maximum:
        raise CloudError("INVALID_OPERATION_INTERVAL")
    return value


def _error_code(value: str) -> str:
    return value if isinstance(value, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", value) else "OPERATION_FAILED"


class OracleRepository:
    def __init__(self, connection_factory, driver=None, *, max_outbox_attempts: int = 5,
                 call_timeout_ms: int = 15000):
        if type(max_outbox_attempts) is not int or not 1 <= max_outbox_attempts <= 10:
            raise CloudError("INVALID_OUTBOX_ATTEMPT_LIMIT")
        if type(call_timeout_ms) is not int or not 1000 <= call_timeout_ms <= 120000:
            raise CloudError("INVALID_DATABASE_TIMEOUT")
        self.connection_factory = connection_factory
        self._driver = driver
        self.max_outbox_attempts = max_outbox_attempts
        self.call_timeout_ms = call_timeout_ms

    @property
    def driver(self):
        if self._driver is None:
            import oracledb
            self._driver = oracledb
        return self._driver

    @contextmanager
    def _session(self):
        source = self.connection_factory
        connection = source() if callable(source) else source.acquire()
        try:
            connection.autocommit = False
            connection.call_timeout = self.call_timeout_ms
            yield connection
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _finish(connection, changed: bool) -> bool:
        if changed:
            connection.commit()
        else:
            connection.rollback()
        return changed

    def ensure_scope(self, scope: dict) -> None:
        scope_id = scope.get("scope_id")
        digest = scope.get("config_sha256")
        ceiling = scope.get("max_requests")
        if (not isinstance(scope_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", scope_id)
                or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
                or type(ceiling) is not int or not 1 <= ceiling <= 10000):
            raise CloudError("INVALID_APPROVAL_SCOPE")
        try:
            with self._session() as connection, connection.cursor() as cursor:
                cursor.execute(f"SELECT migration_version FROM {SCHEMA}.kk_schema_migrations WHERE migration_version='2'")
                if cursor.fetchone() is None:
                    raise CloudError("CLOUD_MIGRATION_REQUIRED")
                cursor.execute(f"SELECT config_sha256, max_requests FROM {SCHEMA}.kk_cloud_scopes WHERE scope_id=:scope_id",
                               scope_id=scope_id)
                existing = _row(cursor)
                if existing:
                    self._check_scope(existing, digest, ceiling)
                    return
                insert(cursor, self.driver, "kk_cloud_scopes", {
                    "scope_id": scope_id, "config_sha256": digest, "max_requests": ceiling,
                })
                connection.commit()
        except Exception as exc:
            if not _unique_violation(exc):
                raise
            # Another approved process inserted the same scope first. Never reset.
            with self._session() as connection, connection.cursor() as cursor:
                cursor.execute(f"SELECT config_sha256, max_requests FROM {SCHEMA}.kk_cloud_scopes WHERE scope_id=:scope_id",
                               scope_id=scope_id)
                existing = _row(cursor)
                if not existing:
                    raise CloudError("SCOPE_CONFLICT") from None
                self._check_scope(existing, digest, ceiling)

    @staticmethod
    def _check_scope(existing: dict, digest: str, ceiling: int) -> None:
        if existing["config_sha256"].strip() != digest or existing["max_requests"] != ceiling:
            raise CloudError("APPROVAL_SCOPE_CONFIG_MISMATCH")

    @staticmethod
    def _find(cursor, case_id: str, idempotency_key: str) -> dict | None:
        cursor.execute(f"""SELECT job_id, status, request_fingerprint, cloud_scope_id AS scope_id
            FROM {SCHEMA}.kk_jobs WHERE case_id=:case_id AND idempotency_key=:idempotency_key""",
                       case_id=case_id, idempotency_key=idempotency_key)
        return _row(cursor)

    def find_submission(self, case_id: str, idempotency_key: str) -> dict | None:
        with self._session() as connection, connection.cursor() as cursor:
            return self._find(cursor, case_id, idempotency_key)

    @staticmethod
    def _reuse(existing: dict, submission: dict) -> dict:
        if (existing.get("request_fingerprint") != submission["request_fingerprint"]
                or existing.get("scope_id") != submission["scope_id"]):
            raise CloudError("IDEMPOTENCY_CONFLICT")
        return {**existing, "created": False}

    def enqueue(self, submission: dict) -> dict:
        """Insert document/job/outbox atomically after immutable object upload.

        A failed DB transaction can leave an unreferenced object; the operator
        workflow uses a deterministic key and must reconcile it, never delete it
        here. Unique-key races return only a matching pre-existing submission.
        """
        try:
            with self._session() as connection, connection.cursor() as cursor:
                existing = self._find(cursor, submission["case_id"], submission["idempotency_key"])
                if existing:
                    return self._reuse(existing, submission)
                insert(cursor, self.driver, "kk_documents", {
                    key: submission[key] for key in ("document_id", "case_id", "sha256", "filename",
                        "mime_type", "byte_length", "object_namespace", "object_bucket", "object_name",
                        "object_version_id", "data_region")
                } | {"source_kind": "OBJECT_STORAGE", "upload_state": "READY"})
                insert(cursor, self.driver, "kk_jobs", {
                    key: submission[key] for key in ("job_id", "document_id", "case_id", "idempotency_key",
                        "request_fingerprint", "model_id", "routing_profile_id", "schema_version", "max_attempts")
                } | {"cloud_scope_id": submission["scope_id"], "processing_mode": "oci", "status": "QUEUED"})
                event = {"schema_version": "1.0", "type": "KK_JOB", "job_id": submission["job_id"],
                         "scope_id": submission["scope_id"]}
                insert(cursor, self.driver, "kk_outbox", {
                    "outbox_id": submission["outbox_id"], "job_id": submission["job_id"],
                    "event_type": "KK_JOB", "event_version": 1, "payload_json": canonical(event),
                }, ("payload_json",))
                connection.commit()
                return {"job_id": submission["job_id"], "status": "QUEUED", "created": True,
                        "request_fingerprint": submission["request_fingerprint"], "scope_id": submission["scope_id"]}
        except Exception as exc:
            if not _unique_violation(exc):
                raise
            existing = self.find_submission(submission["case_id"], submission["idempotency_key"])
            if existing is None:
                raise CloudError("SUBMISSION_UNIQUE_CONFLICT") from None
            return self._reuse(existing, submission)

    def claim_outbox(self, scope_id: str, owner: str, lease_seconds: int) -> dict | None:
        _seconds(lease_seconds)
        token = uuid.uuid4().hex
        with self._session() as connection, connection.cursor() as cursor:
            # Expired publication attempts remain recoverable; an exhausted row
            # becomes visible FAILED work instead of silently hanging forever.
            cursor.execute(f"""UPDATE {SCHEMA}.kk_outbox SET status='FAILED',
                lease_owner=NULL,lease_token=NULL,lease_until=NULL
                WHERE attempt_count>=:maximum AND
                (status='PENDING' OR (status='PUBLISHING' AND lease_until<=SYSTIMESTAMP))
                AND job_id IN (SELECT job_id FROM {SCHEMA}.kk_jobs WHERE cloud_scope_id=:scope_id)""",
                           maximum=self.max_outbox_attempts, scope_id=scope_id)
            # A driver fetch buffer must not lock a batch when this worker asks
            # for one outbox row. Disable prefetch and fetch exactly one row.
            cursor.arraysize = 1
            cursor.prefetchrows = 0
            cursor.execute(f"""SELECT outbox_id,job_id,payload_json,attempt_count
                FROM {SCHEMA}.kk_outbox
                WHERE attempt_count<:maximum AND
                  ((status='PENDING' AND available_at<=SYSTIMESTAMP) OR
                   (status='PUBLISHING' AND lease_until<=SYSTIMESTAMP))
                  AND job_id IN (SELECT job_id FROM {SCHEMA}.kk_jobs WHERE cloud_scope_id=:scope_id)
                ORDER BY created_at,outbox_id FOR UPDATE SKIP LOCKED""",
                           maximum=self.max_outbox_attempts, scope_id=scope_id)
            item = _row(cursor)
            if item is None:
                connection.commit()  # May have marked exhausted rows FAILED.
                return None
            payload = _json_value(item["payload_json"])
            cursor.execute(f"""UPDATE {SCHEMA}.kk_outbox SET status='PUBLISHING',
                attempt_count=attempt_count+1,lease_owner=:owner,lease_token=:token,
                lease_until=SYSTIMESTAMP+NUMTODSINTERVAL(:lease_seconds,'SECOND')
                WHERE outbox_id=:outbox_id""", owner=owner, token=token,
                           lease_seconds=lease_seconds, outbox_id=item["outbox_id"])
            if cursor.rowcount != 1:
                raise CloudError("OUTBOX_CLAIM_CONFLICT")
            connection.commit()
            return {"outbox_id": item["outbox_id"], "job_id": item["job_id"], "payload": payload,
                    "lease_token": token, "attempt_count": int(item["attempt_count"]) + 1}

    def mark_published(self, outbox_id: str, lease_token: str, message_id: str) -> bool:
        with self._session() as connection, connection.cursor() as cursor:
            cursor.execute(f"""UPDATE {SCHEMA}.kk_outbox SET status='PUBLISHED',published_at=SYSTIMESTAMP,
                queue_message_id=:message_id,lease_owner=NULL,lease_token=NULL,lease_until=NULL
                WHERE outbox_id=:outbox_id AND status='PUBLISHING' AND lease_token=:token
                  AND lease_until>SYSTIMESTAMP""", message_id=message_id, outbox_id=outbox_id, token=lease_token)
            return self._finish(connection, cursor.rowcount == 1)

    def release_outbox(self, outbox_id: str, lease_token: str, delay_seconds: int, error_code: str) -> bool:
        _seconds(delay_seconds, zero=True)
        # V1 has no outbox error-text column. The caller records a bounded code in
        # operational telemetry; payloads and exception bodies never enter SQL.
        with self._session() as connection, connection.cursor() as cursor:
            cursor.execute(f"""UPDATE {SCHEMA}.kk_outbox SET
                status=CASE WHEN attempt_count>=:maximum THEN 'FAILED' ELSE 'PENDING' END,
                available_at=SYSTIMESTAMP+NUMTODSINTERVAL(:delay_seconds,'SECOND'),
                lease_owner=NULL,lease_token=NULL,lease_until=NULL
                WHERE outbox_id=:outbox_id AND status='PUBLISHING' AND lease_token=:token
                  AND lease_until>SYSTIMESTAMP""", maximum=self.max_outbox_attempts,
                           delay_seconds=delay_seconds, outbox_id=outbox_id, token=lease_token)
            return self._finish(connection, cursor.rowcount == 1)

    def claim_job(self, job_id: str, scope_id: str, owner: str, lease_seconds: int) -> dict:
        _seconds(lease_seconds)
        with self._session() as connection, connection.cursor() as cursor:
            cursor.execute(f"""SELECT job_id,document_id,case_id,cloud_scope_id,processing_mode AS "MODE",status,version,
                attempt_count,max_attempts,
                CASE WHEN lease_until>SYSTIMESTAMP THEN 1 ELSE 0 END AS lease_active,
                CASE WHEN available_at<=SYSTIMESTAMP THEN 1 ELSE 0 END AS is_ready
                FROM {SCHEMA}.kk_jobs WHERE job_id=:job_id FOR UPDATE WAIT 5""", job_id=job_id)
            job = _row(cursor)
            outcome = None
            if not job:
                outcome = "not_found"
            elif job["cloud_scope_id"] != scope_id or job["mode"] != "oci":
                outcome = "scope_mismatch"
            elif job["status"] in ("NEEDS_REVIEW", "ACCEPTED", "FAILED"):
                outcome = "terminal"
            elif job["status"] == "PROCESSING" and job["lease_active"]:
                outcome = "busy"
            elif job["status"] == "QUEUED" and not job["is_ready"]:
                outcome = "not_ready"
            elif job["status"] not in ("QUEUED", "PROCESSING"):
                raise CloudError("UNEXPECTED_JOB_STATE")
            if outcome:
                connection.rollback()
                return {"outcome": outcome, "job_id": job_id, "status": job["status"] if job else None}
            if job["status"] == "PROCESSING":
                # Expiry does not prove that the previous provider call stopped.
                # Fence the stale owner and require reconciliation, not another
                # paid extraction. This uses the existing job lock/schema.
                cursor.execute(f"""UPDATE {SCHEMA}.kk_jobs SET status='FAILED',
                    error_code='PRIOR_OUTCOME_UNKNOWN',version=version+1,
                    updated_at=SYSTIMESTAMP,completed_at=SYSTIMESTAMP,
                    lease_owner=NULL,lease_token=NULL,lease_until=NULL
                    WHERE job_id=:job_id AND cloud_scope_id=:scope_id
                      AND status='PROCESSING' AND version=:version
                      AND (lease_until<=SYSTIMESTAMP OR lease_until IS NULL)""",
                               job_id=job_id, scope_id=scope_id, version=job["version"])
                if cursor.rowcount != 1:
                    raise CloudError("JOB_CLAIM_CONFLICT")
                connection.commit()
                return {"outcome": "terminal", "job_id": job_id, "status": "FAILED",
                        "code": "PRIOR_OUTCOME_UNKNOWN"}
            if job["attempt_count"] >= job["max_attempts"]:
                cursor.execute(f"""UPDATE {SCHEMA}.kk_jobs SET status='FAILED',error_code='ATTEMPTS_EXHAUSTED',
                    version=version+1,updated_at=SYSTIMESTAMP,completed_at=SYSTIMESTAMP,
                    lease_owner=NULL,lease_token=NULL,lease_until=NULL WHERE job_id=:job_id""", job_id=job_id)
                connection.commit()
                return {"outcome": "terminal", "job_id": job_id, "status": "FAILED"}
            # All claimers lock job then scope, and release both before remote I/O.
            cursor.execute(f"""SELECT max_requests,used_requests FROM {SCHEMA}.kk_cloud_scopes
                WHERE scope_id=:scope_id FOR UPDATE WAIT 5""", scope_id=scope_id)
            scope = _row(cursor)
            if not scope:
                raise CloudError("SCOPE_NOT_INITIALIZED")
            if scope["used_requests"] >= scope["max_requests"]:
                connection.rollback()
                return {"outcome": "budget_exhausted", "job_id": job_id}
            cursor.execute(f"""SELECT sha256,filename,mime_type,byte_length,object_namespace,
                object_bucket,object_name,object_version_id,data_region,upload_state
                FROM {SCHEMA}.kk_documents WHERE document_id=:document_id""", document_id=job["document_id"])
            document = _row(cursor)
            if not document or document["upload_state"] != "READY":
                raise CloudError("SOURCE_NOT_READY")
            token = uuid.uuid4().hex
            cursor.execute(f"""UPDATE {SCHEMA}.kk_cloud_scopes SET used_requests=used_requests+1,
                updated_at=SYSTIMESTAMP WHERE scope_id=:scope_id AND used_requests<max_requests""", scope_id=scope_id)
            if cursor.rowcount != 1:
                raise CloudError("SCOPE_RESERVATION_CONFLICT")
            cursor.execute(f"""UPDATE {SCHEMA}.kk_jobs SET status='PROCESSING',version=version+1,
                attempt_count=attempt_count+1,lease_owner=:owner,lease_token=:token,
                lease_until=SYSTIMESTAMP+NUMTODSINTERVAL(:lease_seconds,'SECOND'),
                error_code=NULL,updated_at=SYSTIMESTAMP
                WHERE job_id=:job_id AND version=:version""", owner=owner, token=token,
                           lease_seconds=lease_seconds, job_id=job_id, version=job["version"])
            if cursor.rowcount != 1:
                raise CloudError("JOB_CLAIM_CONFLICT")
            connection.commit()
            return {**job, **document, "outcome": "claimed", "scope_id": scope_id, "status": "PROCESSING",
                    "lease_token": token, "lease_owner": owner, "version": int(job["version"]) + 1,
                    "attempt_count": int(job["attempt_count"]) + 1}

    @staticmethod
    def _lock_live_claim(cursor, claim: dict) -> bool:
        cursor.execute(f"""SELECT job_id FROM {SCHEMA}.kk_jobs
            WHERE job_id=:job_id AND cloud_scope_id=:scope_id AND status='PROCESSING'
              AND version=:version AND lease_token=:token AND lease_until>SYSTIMESTAMP
            FOR UPDATE WAIT 5""", job_id=claim["job_id"], scope_id=claim["scope_id"],
                       version=claim["version"], token=claim["lease_token"])
        return cursor.fetchone() is not None

    def complete_job(self, claim: dict, payload: dict, validation: dict, metadata: dict) -> bool:
        assert_valid_payload(payload)
        # Candidate-provided validation is untrusted. Recompute before the tx.
        validation = {"issues": validate_payload(payload)}
        if not isinstance(metadata, dict) or len(canonical(metadata).encode("utf-8")) > 65536:
            raise CloudError("PROCESSING_METADATA_INVALID")
        with self._session() as connection, connection.cursor() as cursor:
            if not self._lock_live_claim(cursor, claim):
                return self._finish(connection, False)
            next_version = int(claim["version"]) + 1
            write_snapshot(cursor, self.driver, extraction_id=uuid.uuid4().hex,
                           job_id=claim["job_id"], job_version=next_version, payload=payload,
                           validation=validation, source_kind="MODEL", processing_metadata=metadata)
            cursor.execute(f"""UPDATE {SCHEMA}.kk_jobs SET status='NEEDS_REVIEW',version=:next_version,
                current_extraction_version=:next_version,updated_at=SYSTIMESTAMP,completed_at=SYSTIMESTAMP,
                lease_owner=NULL,lease_token=NULL,lease_until=NULL,error_code=NULL
                WHERE job_id=:job_id AND cloud_scope_id=:scope_id AND status='PROCESSING'
                  AND version=:version AND lease_token=:token AND lease_until>SYSTIMESTAMP""",
                           next_version=next_version, job_id=claim["job_id"], scope_id=claim["scope_id"],
                           version=claim["version"], token=claim["lease_token"])
            return self._finish(connection, cursor.rowcount == 1)

    def fail_job(self, claim: dict, error_code: str, retryable: bool, delay_seconds: int) -> bool:
        _seconds(delay_seconds, zero=True)
        with self._session() as connection, connection.cursor() as cursor:
            cursor.execute(f"""UPDATE {SCHEMA}.kk_jobs SET
                status=CASE WHEN :retryable=1 AND attempt_count<max_attempts THEN 'QUEUED' ELSE 'FAILED' END,
                available_at=SYSTIMESTAMP+NUMTODSINTERVAL(:delay_seconds,'SECOND'),
                completed_at=CASE WHEN :retryable=1 AND attempt_count<max_attempts THEN NULL ELSE SYSTIMESTAMP END,
                version=version+1,error_code=:error_code,updated_at=SYSTIMESTAMP,
                lease_owner=NULL,lease_token=NULL,lease_until=NULL
                WHERE job_id=:job_id AND cloud_scope_id=:scope_id AND status='PROCESSING'
                  AND version=:version AND lease_token=:token AND lease_until>SYSTIMESTAMP""",
                           retryable=1 if retryable else 0, delay_seconds=delay_seconds,
                           error_code=_error_code(error_code), job_id=claim["job_id"],
                           scope_id=claim["scope_id"], version=claim["version"], token=claim["lease_token"])
            return self._finish(connection, cursor.rowcount == 1)

    def scope_status(self, scope_id: str) -> dict:
        with self._session() as connection, connection.cursor() as cursor:
            cursor.execute(f"""SELECT scope_id,max_requests,used_requests FROM {SCHEMA}.kk_cloud_scopes
                WHERE scope_id=:scope_id""", scope_id=scope_id)
            scope = _row(cursor)
            if not scope:
                raise CloudError("SCOPE_NOT_INITIALIZED")
            cursor.execute(f"""SELECT status,COUNT(*) AS job_count FROM {SCHEMA}.kk_jobs
                WHERE cloud_scope_id=:scope_id GROUP BY status""", scope_id=scope_id)
            counts = {}
            while (row := _row(cursor)) is not None:
                counts[row["status"]] = int(row["job_count"])
            return {"scope_id": scope["scope_id"], "max_requests": int(scope["max_requests"]),
                    "used_requests": int(scope["used_requests"]), "jobs_by_status": counts}
