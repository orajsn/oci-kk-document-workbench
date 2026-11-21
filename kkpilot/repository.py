"""SQLite WAL queue with transactional claims, lease fencing and review history."""

from __future__ import annotations

import json
import math
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .config import MODE_LABELS
from .validation import assert_valid_payload, validate_payload
from .telemetry import safe_workflow_timing


STATES = ("QUEUED", "PROCESSING", "NEEDS_REVIEW", "ACCEPTED", "FAILED")


class DomainError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def safe_refinement_metadata(value: dict) -> dict:
    """Allow only bounded, non-content facts about focused refinement."""
    result = {}
    maximum_calls = value.get("maximum_provider_calls_per_job")
    if type(maximum_calls) is int and maximum_calls in (1, 2, 3, 5):
        result["maximum_provider_calls_per_job"] = maximum_calls
    status = value.get("refinement_status")
    if isinstance(status, str) and status in {"OFF", "APPLIED", "GEOMETRY_UNAVAILABLE", "ROW_MISMATCH", "FAILED", "UNKNOWN_OUTCOME"}:
        result["refinement_status"] = status
    for key, maximum in (("provider_calls_attempted", 5), ("refinement_value_changes", 200),
                         ("hybrid_nik_accepted_count", 100), ("hybrid_nik_held_count", 100),
                         ("field_corroboration_changed_count", 200),
                         ("field_corroboration_held_count", 200),
                         ("field_corroboration_unchanged_count", 200),
                         ("three_way_nik_changed_count", 100),
                         ("three_way_nik_held_count", 100),
                         ("three_way_nik_unchanged_count", 100),
                         ("three_way_nik_du_calls_attempted", 1),
                         ("three_way_nik_model_calls_attempted", 1)):
        item = value.get(key)
        if type(item) is int and 0 <= item <= maximum:
            result[key] = item
    elapsed = value.get("refinement_model_call_seconds")
    if type(elapsed) in (int, float) and math.isfinite(elapsed) and elapsed >= 0:
        result["refinement_model_call_seconds"] = elapsed
    if value.get("refinement_contract") == "kk-focused-identity-place-1.0":
        result["refinement_contract"] = "kk-focused-identity-place-1.0"
    status = value.get("hybrid_status")
    if isinstance(status, str) and status in {"APPLIED", "HELD", "UNKNOWN_OUTCOME", "SKIPPED_FOCUS"}:
        result["hybrid_status"] = status
    status = value.get("field_corroboration_status")
    if isinstance(status, str) and status in {"OFF", "APPLIED", "HELD"}:
        result["field_corroboration_status"] = status
    status = value.get("three_way_nik_status")
    if isinstance(status, str) and status in {"OFF", "APPLIED", "HELD", "NO_PROPOSAL"}:
        result["three_way_nik_status"] = status
    reason = value.get("three_way_nik_reason")
    if isinstance(reason, str) and reason in {
            "PENDING", "COMPLETE", "PREREQUISITE_UNAVAILABLE", "GEOMETRY_UNAVAILABLE",
            "DU_UNAVAILABLE", "DU_RESPONSE_INVALID", "NIK_CALL_UNAVAILABLE", "NIK_RESPONSE_INVALID",
            "ROWSET_MISMATCH", "POLICY_UNAVAILABLE", "DIAGNOSTIC_UNAVAILABLE", "NO_ELIGIBLE_PROPOSAL"}:
        result["three_way_nik_reason"] = reason
    status = value.get("row_alignment_status")
    if isinstance(status, str) and status in {"OFF", "AVAILABLE", "UNAVAILABLE"}:
        result["row_alignment_status"] = status
    angle = value.get("row_alignment_angle_degrees")
    if status == "AVAILABLE" and type(angle) in (int, float) and -8 <= angle <= 8 and math.isfinite(angle):
        result["row_alignment_angle_degrees"] = angle
    if value.get("row_alignment_method") == "grid-angle-1.0":
        result["row_alignment_method"] = "grid-angle-1.0"
    status = value.get("local_ocr_prefetch_status")
    if isinstance(status, str) and status in {"CONSUMED", "UNUSED", "FAILED"}:
        result["local_ocr_prefetch_status"] = status
    runtime = value.get('local_ocr_runtime')
    if isinstance(runtime, str) and runtime in {'WARM_SESSION', 'WARM_FAILED', 'COLD_FALLBACK'}:
        result['local_ocr_runtime'] = runtime
    index = value.get('local_ocr_session_request_index')
    if type(index) is int and 1 <= index <= 10000:
        result['local_ocr_session_request_index'] = index
    startup = value.get('local_ocr_startup_seconds')
    if type(startup) in (int, float) and math.isfinite(startup) and 0 <= startup <= 180:
        result['local_ocr_startup_seconds'] = startup
    for key in ("local_ocr_seconds", "hybrid_model_call_seconds", "row_alignment_seconds",
                "three_way_nik_seconds", "three_way_nik_du_seconds", "three_way_nik_model_call_seconds",
                "local_ocr_wait_seconds", "local_ocr_overlap_seconds", "local_ocr_cleanup_wait_seconds"):
        elapsed = value.get(key)
        if type(elapsed) in (int, float) and 0 <= elapsed <= 86400 and math.isfinite(elapsed):
            result[key] = elapsed
    return result


def safe_processing_metadata(value: object) -> dict:
    """Keep bounded operational facts only, never model text or configuration."""
    if not isinstance(value, dict):
        return {}
    result = {}
    timing = safe_workflow_timing(value.get("workflow_timing"))
    if timing:
        result["workflow_timing"] = timing
    patterns = {
        "mode": r"(?:demo|reference|oci)",
        "entry_region": r"[a-z]{2}-[a-z0-9-]+-[1-9][0-9]*",
        "served_region": r"[a-z]{2}-[a-z0-9-]+-[1-9][0-9]*",
        "request_id": r"(?:[A-Za-z0-9_-]{1,200}|[A-Fa-f0-9]{16,128}(?:/[A-Fa-f0-9]{16,128}){1,4})",
        "model": r"(?:(?:cohere|meta)\.[A-Za-z0-9_.:-]{1,160}|google\.gemini-2\.5-pro)",
        "prompt_version": r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}",
        "schema_version": r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}",
    }
    for key, pattern in patterns.items():
        item = value.get(key)
        if isinstance(item, str) and re.fullmatch(pattern, item) and not item.startswith("ocid1."):
            result[key] = item
    elapsed = value.get("elapsed_seconds")
    if type(elapsed) in (int, float) and 0 <= elapsed <= 86400 and math.isfinite(elapsed):
        result["elapsed_seconds"] = elapsed
    if type(value.get("live_test")) is bool:
        result["live_test"] = value["live_test"]
    if (result.get("model") == "google.gemini-2.5-pro"
            and result.get("entry_region") == "ap-batam-1"
            and result.get("served_region") == "us-chicago-1"
            and value.get("pipeline") == "gemini_image_only"
            and value.get("external_processor") == "Google"
            and value.get("external_processing_scope") == "Google Americas"):
        result.update(pipeline="gemini_image_only", external_processor="Google",
                      external_processing_scope="Google Americas", ocr_used=value.get("ocr_used") is True)
        result.update(safe_refinement_metadata(value))
        for key in ("preparation_seconds", "model_call_seconds"):
            item = value.get(key)
            if type(item) in (int, float) and math.isfinite(item) and 0 <= item <= 3600:
                result[key] = item
    preprocessing = value.get("preprocessing")
    if isinstance(preprocessing, dict):
        cleaned = {}
        for key in ("original_size", "oriented_size", "sent_size"):
            size = preprocessing.get(key)
            if isinstance(size, (tuple, list)) and len(size) == 2 and all(type(number) is int and 0 < number <= 100_000 for number in size):
                cleaned[key] = list(size)
        for key in ("crop_applied", "automatic_localization"):
            if type(preprocessing.get(key)) is bool:
                cleaned[key] = preprocessing[key]
        for key in ("exif_orientation", "rotation_degrees"):
            if type(preprocessing.get(key)) is int and 0 <= preprocessing[key] <= 360:
                cleaned[key] = preprocessing[key]
        version = preprocessing.get("preprocessing_version")
        if isinstance(version, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", version):
            cleaned["preprocessing_version"] = version
        result["preprocessing"] = cleaned
    response = value.get("response")
    if isinstance(response, dict):
        cleaned = {}
        finish = response.get("finish_reason")
        if isinstance(finish, str) and re.fullmatch(r"[A-Z_]{1,40}", finish.upper()):
            cleaned["finish_reason"] = finish
        usage = response.get("usage")
        usage_keys = ("input_tokens", "output_tokens", "total_tokens", "prompt_tokens", "completion_tokens")
        if isinstance(usage, dict):
            counters = {key: usage[key] for key in usage_keys if type(usage.get(key)) is int and 0 <= usage[key] <= 1_000_000_000}
            for key in ("billed_units", "tokens"):
                nested = usage.get(key)
                if isinstance(nested, dict):
                    counters[key] = {name: nested[name] for name in usage_keys if type(nested.get(name)) is int and 0 <= nested[name] <= 1_000_000_000}
            cleaned["usage"] = counters
        result["response"] = cleaned
    return result


class Repository:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=15, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=15000")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    @contextmanager
    def _connection(self, *, write: bool = False):
        connection = self._connect()
        try:
            if write:
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            if write:
                connection.commit()
        except BaseException:
            if write:
                connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            if connection.execute("PRAGMA user_version").fetchone()[0] > 2:
                raise DomainError("DATABASE_VERSION", "This database requires a newer local pilot version.", 500)
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    mode TEXT NOT NULL CHECK(mode IN ('demo','reference','oci')),
                    image_relpath TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('QUEUED','PROCESSING','NEEDS_REVIEW','ACCEPTED','FAILED')),
                    version INTEGER NOT NULL DEFAULT 1,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL,
                    available_at REAL NOT NULL,
                    lease_token TEXT,
                    lease_expires REAL,
                    extraction_json TEXT,
                    validation_json TEXT NOT NULL DEFAULT '{"issues":[]}',
                    processing_metadata_json TEXT NOT NULL DEFAULT '{}',
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(case_id,idempotency_key)
                );
                CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(status,available_at,created_at);
                CREATE TABLE IF NOT EXISTS review_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL REFERENCES jobs(id),
                    from_version INTEGER NOT NULL,
                    to_version INTEGER NOT NULL,
                    decision TEXT NOT NULL,
                    comment TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    previous_extraction_json TEXT,
                    extraction_json TEXT NOT NULL,
                    validation_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
            """)
            self._migrate_v2(connection)

    @staticmethod
    def _migrate_v2(connection: sqlite3.Connection) -> None:
        schema = connection.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='jobs'").fetchone()[0]
        columns = [row[1] for row in connection.execute("PRAGMA table_info(jobs)")]
        if "'oci'" in schema and "processing_metadata_json" in columns:
            connection.execute("PRAGMA user_version=2")
            return
        # Rebuild the CHECK constraint without renaming the referenced old table.
        # FK enforcement is restored after a transactional copy and integrity check.
        connection.execute("PRAGMA foreign_keys=OFF")
        try:
            connection.execute("BEGIN IMMEDIATE")
            new_schema = re.sub(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"`]?jobs[\"`]?", "CREATE TABLE jobs_v2", schema, count=1, flags=re.IGNORECASE)
            new_schema = re.sub(r"mode\s+IN\s*\(\s*'demo'\s*,\s*'reference'\s*\)", "mode IN ('demo','reference','oci')", new_schema, flags=re.IGNORECASE)
            if "'oci'" not in new_schema or new_schema == schema:
                raise DomainError("DATABASE_MIGRATION", "Existing database schema cannot be migrated safely.", 500)
            connection.execute(new_schema)
            if "processing_metadata_json" not in columns:
                connection.execute("ALTER TABLE jobs_v2 ADD COLUMN processing_metadata_json TEXT NOT NULL DEFAULT '{}'")
            names = ",".join('"' + name.replace('"', '""') + '"' for name in columns)
            connection.execute(f"INSERT INTO jobs_v2 ({names}) SELECT {names} FROM jobs")
            connection.execute("DROP TABLE jobs")
            connection.execute("ALTER TABLE jobs_v2 RENAME TO jobs")
            connection.execute("CREATE INDEX jobs_queue ON jobs(status,available_at,created_at)")
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise DomainError("DATABASE_MIGRATION", "Database migration failed its relationship check.", 500)
            connection.execute("PRAGMA user_version=2")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.execute("PRAGMA foreign_keys=ON")

    @staticmethod
    def _summary(row: sqlite3.Row) -> dict:
        keys = ("id", "case_id", "filename", "mode", "sha256", "status", "version", "attempts", "max_attempts", "last_error", "created_at", "updated_at")
        summary = {key: row[key] for key in keys}
        summary["processing_label"] = MODE_LABELS[row["mode"]]
        return summary

    def _detail(self, connection: sqlite3.Connection, job_id: str) -> dict:
        row = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise DomainError("NOT_FOUND", "Job was not found.", 404)
        history = []
        for review in connection.execute("SELECT * FROM review_history WHERE job_id=? ORDER BY id", (job_id,)):
            history.append({
                "id": review["id"], "from_version": review["from_version"], "to_version": review["to_version"],
                "decision": review["decision"], "comment": review["comment"], "actor": review["actor"],
                "created_at": review["created_at"],
            })
        return {
            "job": self._summary(row),
            "extraction": json.loads(row["extraction_json"]) if row["extraction_json"] else None,
            "validation": json.loads(row["validation_json"]),
            "processing_metadata": json.loads(row["processing_metadata_json"]),
            "review_history": history,
        }

    def create_job(self, *, case_id: str, filename: str, mode: str, image_relpath: str, sha256: str,
                   idempotency_key: str, request_hash: str, max_attempts: int = 3) -> tuple[dict, bool]:
        with self._connection(write=True) as connection:
            previous = connection.execute("SELECT id,request_hash FROM jobs WHERE case_id=? AND idempotency_key=?", (case_id, idempotency_key)).fetchone()
            if previous:
                if previous["request_hash"] != request_hash:
                    raise DomainError("IDEMPOTENCY_CONFLICT", "This case and idempotency key already identify different content or options.", 409)
                return self._detail(connection, previous["id"]), False
            job_id, timestamp = uuid.uuid4().hex, _utc()
            connection.execute("""INSERT INTO jobs
                (id,case_id,filename,mode,image_relpath,sha256,idempotency_key,request_hash,status,max_attempts,available_at,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,'QUEUED',?,?,?,?)""",
                (job_id, case_id, filename, mode, image_relpath, sha256, idempotency_key, request_hash, max_attempts, time.time(), timestamp, timestamp))
            return self._detail(connection, job_id), True

    def get_job(self, job_id: str) -> dict:
        with self._connection() as connection:
            connection.execute("BEGIN")
            return self._detail(connection, job_id)

    def image_record(self, job_id: str) -> dict:
        with self._connection() as connection:
            row = connection.execute("SELECT image_relpath,sha256 FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise DomainError("NOT_FOUND", "Job was not found.", 404)
            return dict(row)

    def list_jobs(self, status: str | None = None, query: str | None = None) -> list[dict]:
        where, params = [], []
        if status:
            if status not in STATES:
                raise DomainError("INVALID_STATUS", "Unsupported status filter.")
            where.append("status=?")
            params.append(status)
        if query:
            if len(query) > 200:
                raise DomainError("INVALID_QUERY", "Search query is too long.")
            # instr is literal, so percent/underscore do not become SQL wildcards.
            where.append("(instr(lower(case_id),lower(?))>0 OR instr(lower(filename),lower(?))>0 OR instr(id,?)>0)")
            params.extend((query, query, query))
        sql = "SELECT * FROM jobs" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY created_at DESC,id DESC LIMIT 500"
        with self._connection() as connection:
            return [self._summary(row) for row in connection.execute(sql, params)]

    def claim_job(self, lease_seconds: float = 120) -> dict | None:
        now, timestamp = time.time(), _utc()
        with self._connection(write=True) as connection:
            connection.execute("""UPDATE jobs SET status=CASE WHEN attempts<max_attempts THEN 'QUEUED' ELSE 'FAILED' END,
                lease_token=NULL,lease_expires=NULL,version=version+1,last_error='LEASE_EXPIRED',available_at=?,updated_at=?
                WHERE status='PROCESSING' AND lease_expires<=?""", (now, timestamp, now))
            row = connection.execute("SELECT * FROM jobs WHERE status='QUEUED' AND available_at<=? AND attempts<max_attempts ORDER BY created_at,id LIMIT 1", (now,)).fetchone()
            if row is None:
                return None
            lease_token = uuid.uuid4().hex
            connection.execute("""UPDATE jobs SET status='PROCESSING',attempts=attempts+1,version=version+1,
                lease_token=?,lease_expires=?,updated_at=?,last_error=NULL WHERE id=?""",
                (lease_token, now + lease_seconds, timestamp, row["id"]))
            return dict(connection.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone())

    def heartbeat(self, job_id: str, lease_token: str, lease_seconds: float = 120) -> bool:
        now = time.time()
        with self._connection(write=True) as connection:
            cursor = connection.execute("UPDATE jobs SET lease_expires=? WHERE id=? AND status='PROCESSING' AND lease_token=? AND lease_expires>?", (now + lease_seconds, job_id, lease_token, now))
            return cursor.rowcount == 1

    def complete_job(self, job_id: str, lease_token: str, extraction: dict, validation: dict, processing_metadata: dict | None = None) -> bool:
        assert_valid_payload(extraction)
        # Recompute even for internal callers: validation supplied by an adapter is untrusted.
        validation = {"issues": validate_payload(extraction)}
        with self._connection(write=True) as connection:
            cursor = connection.execute("""UPDATE jobs SET status='NEEDS_REVIEW',extraction_json=?,validation_json=?,processing_metadata_json=?,
                lease_token=NULL,lease_expires=NULL,version=version+1,last_error=NULL,updated_at=?
                WHERE id=? AND status='PROCESSING' AND lease_token=? AND lease_expires>?""",
                (_json(extraction), _json(validation), _json(safe_processing_metadata(processing_metadata)), _utc(), job_id, lease_token, time.time()))
            return cursor.rowcount == 1

    def fail_job(self, job_id: str, lease_token: str, error_code: str, retryable: bool = True, retry_delay: float = 0) -> bool:
        if not isinstance(error_code, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", error_code):
            error_code = "EXTRACTION_FAILED"
        with self._connection(write=True) as connection:
            cursor = connection.execute("""UPDATE jobs SET status=CASE WHEN ? AND attempts<max_attempts THEN 'QUEUED' ELSE 'FAILED' END,
                available_at=?,lease_token=NULL,lease_expires=NULL,version=version+1,last_error=?,updated_at=?
                WHERE id=? AND status='PROCESSING' AND lease_token=? AND lease_expires>?""",
                (bool(retryable), time.time() + retry_delay, error_code, _utc(), job_id, lease_token, time.time()))
            return cursor.rowcount == 1

    def retry_job(self, job_id: str) -> dict:
        with self._connection(write=True) as connection:
            row = connection.execute("SELECT status,attempts,max_attempts FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise DomainError("NOT_FOUND", "Job was not found.", 404)
            if row["status"] != "FAILED":
                raise DomainError("INVALID_STATE", "Only failed jobs can be retried.", 409)
            if row["attempts"] >= row["max_attempts"]:
                raise DomainError("RETRY_LIMIT", "The retry limit has been reached. Create a new job only after checking the cause.", 409)
            connection.execute("UPDATE jobs SET status='QUEUED',available_at=?,version=version+1,last_error=NULL,updated_at=? WHERE id=?", (time.time(), _utc(), job_id))
            return self._detail(connection, job_id)

    def review_job(self, job_id: str, expected_version: int, decision: str, extraction: dict,
                   validation: dict, comment: str) -> dict:
        if type(expected_version) is not int or expected_version < 1:
            raise DomainError("INVALID_VERSION", "expected_version must be a positive integer.")
        if decision not in ("ACCEPTED", "NEEDS_REVIEW"):
            raise DomainError("INVALID_DECISION", "Review decision must be ACCEPTED or NEEDS_REVIEW.")
        if not isinstance(comment, str) or len(comment) > 4000 or "\x00" in comment:
            raise DomainError("INVALID_COMMENT", "Comment must be text up to 4000 characters.")
        try:
            comment.encode("utf-8")
        except UnicodeError:
            raise DomainError("INVALID_COMMENT", "Comment must contain valid Unicode text.") from None
        validation = {"issues": validate_payload(extraction)}
        if decision == "ACCEPTED" and any(issue["severity"] == "ERROR" for issue in validation["issues"]):
            raise DomainError("REVIEW_BLOCKED", "Resolve validation errors and unreviewed fields before acceptance.", 422)
        if decision == "ACCEPTED" and any(issue["code"] == "LEGACY_IDENTIFIER_REVIEW" for issue in validation["issues"]) and not comment.strip():
            raise DomainError("REVIEW_COMMENT_REQUIRED", "Record your source-image check of legacy identifiers in a review comment before acceptance.", 422)
        with self._connection(write=True) as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise DomainError("NOT_FOUND", "Job was not found.", 404)
            if row["version"] != expected_version:
                raise DomainError("VERSION_CONFLICT", "Another update changed this job. Reload before saving your review.", 409)
            if row["status"] not in ("NEEDS_REVIEW", "ACCEPTED"):
                raise DomainError("INVALID_STATE", "Only extracted jobs can be reviewed.", 409)
            timestamp, next_version = _utc(), expected_version + 1
            connection.execute("""INSERT INTO review_history
                (job_id,from_version,to_version,decision,comment,actor,previous_extraction_json,extraction_json,validation_json,created_at)
                VALUES (?,?,?,?,?,'local_session',?,?,?,?)""",
                (job_id, expected_version, next_version, decision, comment, row["extraction_json"], _json(extraction), _json(validation), timestamp))
            connection.execute("UPDATE jobs SET status=?,version=?,extraction_json=?,validation_json=?,updated_at=? WHERE id=?", (decision, next_version, _json(extraction), _json(validation), timestamp, job_id))
            return self._detail(connection, job_id)

    def export_job(self, job_id: str) -> dict:
        with self._connection() as connection:
            connection.execute("BEGIN")
            detail = self._detail(connection, job_id)
            if detail["extraction"] is None:
                raise DomainError("NOT_READY", "This job has no extraction to export.", 409)
            detail["export"] = {
                "schema_version": "1.0", "exported_at": _utc(),
                "source_sha256": detail["job"]["sha256"], "job_version": detail["job"]["version"],
                "processing_mode": detail["job"]["mode"], "is_ai_extraction": detail["job"]["mode"] == "oci",
                "human_accepted": detail["job"]["status"] == "ACCEPTED",
            }
            detail["review_history"] = [dict(row) for row in connection.execute(
                "SELECT id,from_version,to_version,decision,comment,actor,previous_extraction_json,extraction_json,validation_json,created_at FROM review_history WHERE job_id=? ORDER BY id", (job_id,))]
            for row in detail["review_history"]:
                for field in ("previous_extraction", "extraction", "validation"):
                    encoded = row.pop(field + "_json")
                    row[field] = json.loads(encoded) if encoded else None
            return detail
