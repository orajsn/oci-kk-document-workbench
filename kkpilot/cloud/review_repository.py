"""Scoped Oracle reads and atomic human review for the SSH operator pilot.

The configured reviewer name identifies the named operator of this deployment;
it is not an authenticated end-user identity. Each operation acquires its own
connection. This module never opens an OCI client, creates a PAR or changes DDL.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import copy
import re
import uuid

from ..config import MODE_LABELS
from ..repository import DomainError, STATES, safe_processing_metadata
from ..validation import PayloadError, validate_payload
from .shared_writer import SCHEMA, insert, write_snapshot
from .config import valid_prior_review_scopes


_JOB_COLUMNS = f"""
    j.job_id AS id, j.case_id, d.filename, d.sha256, j.status,
    j.version, j.attempt_count AS attempts, j.max_attempts,
    CASE WHEN j.status='QUEUED' AND j.attempt_count=0 AND EXISTS (
        SELECT 1 FROM {SCHEMA}.kk_outbox o WHERE o.job_id=j.job_id
          AND o.event_type='KK_JOB' AND o.event_version=1 AND o.status='FAILED'
    ) THEN 1 ELSE 0 END AS dispatch_unconfirmed,
    j.error_code AS last_error, SYS_EXTRACT_UTC(j.created_at) AS created_at,
    SYS_EXTRACT_UTC(j.updated_at) AS updated_at
"""
_JOB_FROM = f"""FROM {SCHEMA}.kk_jobs j
    JOIN {SCHEMA}.kk_documents d ON d.document_id=j.document_id AND d.case_id=j.case_id
"""
_OPEN_NIK_BY_JOB = f"""OUTER APPLY (
                    SELECT COUNT(*) AS open_count,
                           MIN(c.field_key) KEEP (DENSE_RANK FIRST ORDER BY c.member_row,c.field_key) AS first_key
                    FROM {SCHEMA}.kk_nik_review_cases c
                    WHERE c.job_id=j.job_id AND NOT EXISTS (
                        SELECT 1 FROM {SCHEMA}.kk_nik_review_events v WHERE v.field_key=c.field_key)
                ) nr"""
_SCOPE = "j.cloud_scope_id=:scope_id AND j.processing_mode='oci'"


def _row(cursor) -> dict | None:
    value = cursor.fetchone()
    if value is None:
        return None
    if isinstance(value, dict):
        return {str(key).lower(): item for key, item in value.items()}
    return {column[0].lower(): item for column, item in zip(cursor.description, value)}


def _rows(cursor):
    while (row := _row(cursor)) is not None:
        yield row


def _text(value):
    return value.read() if hasattr(value, "read") else value


def _stored_json(value, default=None):
    if value is None:
        return default
    if isinstance(value, dict):
        # Newer Oracle drivers can return JSON-constrained columns as objects.
        return copy.deepcopy(value)
    try:
        decoded = json.loads(_text(value))
    except (TypeError, ValueError, UnicodeError):
        raise DomainError("STORED_RESULT_INVALID", "The stored result could not be read.", 500) from None
    if not isinstance(decoded, dict):
        raise DomainError("STORED_RESULT_INVALID", "The stored result could not be read.", 500)
    return decoded


def _timestamp(value):
    if isinstance(value, datetime):
        # SELECT statements normalize database timestamps with SYS_EXTRACT_UTC.
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat(timespec="milliseconds")
    return value


def _not_found():
    return DomainError("NOT_FOUND", "Job was not found.", 404)


def _job_id(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 36 or "\x00" in value:
        raise _not_found()
    return value


class CloudReviewRepository:
    """UI repository for one approved cloud scope and one configured operator."""

    def __init__(self, connection_factory, driver, scope_id: str, reviewer_identity: str,
                 prior_review_scope_ids=None):
        if not isinstance(scope_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", scope_id):
            raise ValueError("A valid approved cloud scope is required.")
        prior = [] if prior_review_scope_ids is None else prior_review_scope_ids
        if not valid_prior_review_scopes(prior, scope_id):
            raise ValueError("Invalid prior review scope allowlist.")
        if (not isinstance(reviewer_identity, str) or not reviewer_identity.strip()
                or len(reviewer_identity) > 256 or "\x00" in reviewer_identity):
            raise ValueError("A configured named operator is required.")
        try:
            reviewer_identity.encode("utf-8")
        except UnicodeError:
            raise ValueError("The configured operator name must be valid Unicode.") from None
        self.connection_factory = connection_factory
        self.driver = driver
        self.scope_id = scope_id
        self.prior_review_scope_ids = tuple(prior)
        self._scope_binds = {"scope_id": scope_id, **{
            f"prior_scope_{index}": scope for index, scope in enumerate(prior)}}
        placeholders = [":scope_id"] + [f":prior_scope_{index}" for index in range(len(prior))]
        self._scope = (_SCOPE if not prior else
                       f"j.cloud_scope_id IN ({','.join(placeholders)}) AND j.processing_mode='oci'")
        self.reviewer_identity = reviewer_identity

    @contextmanager
    def _session(self, *, write=False):
        connection = None
        committed = False
        try:
            source = self.connection_factory
            connection = source() if callable(source) else source.acquire()
            connection.autocommit = False
            connection.call_timeout = 15000
            if not write:
                # All detail/history/export statements see the same Oracle SCN.
                with connection.cursor() as cursor:
                    cursor.execute("SET TRANSACTION READ ONLY")
            yield connection
            if write:
                connection.commit()
                committed = True
        except DomainError:
            raise
        except Exception:
            # Never return database messages, DSNs, credentials or SQL to the UI.
            raise DomainError("DATABASE_UNAVAILABLE", "The review database is temporarily unavailable.", 503) from None
        finally:
            if connection is not None:
                # End read-only or failed transactions before returning sessions.
                if not committed:
                    try:
                        connection.rollback()
                    except Exception:
                        pass
                try:
                    connection.close()
                except Exception:
                    pass

    @staticmethod
    def _summary(row):
        result = {key: row[key] for key in (
            "id", "case_id", "filename", "sha256", "status", "version", "attempts",
            "max_attempts", "last_error", "created_at", "updated_at",
        )}
        result["sha256"] = result["sha256"].strip()
        result["mode"] = "oci"
        result["processing_label"] = MODE_LABELS["oci"]
        for key in ("version", "attempts", "max_attempts"):
            result[key] = int(result[key])
        for key in ("created_at", "updated_at"):
            result[key] = _timestamp(result[key])
        # The database stores operation codes; do not surface arbitrary error text.
        error = result["last_error"]
        if error is not None and (not isinstance(error, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,99}", error)):
            result["last_error"] = "OPERATION_FAILED"
        if row.get("dispatch_unconfirmed") == 1:
            result["dispatch_unconfirmed"] = True
        return result

    def _detail(self, connection, job_id, *, full_history=False):
        binds = {**self._scope_binds, "job_id": job_id}
        with connection.cursor() as cursor:
            cursor.execute(f"""SELECT {_JOB_COLUMNS}, e.payload_json, e.validation_json,
                    e.processing_metadata_json
                {_JOB_FROM}
                LEFT JOIN {SCHEMA}.kk_extractions e
                    ON e.job_id=j.job_id AND e.job_version=j.current_extraction_version
                WHERE {self._scope} AND j.job_id=:job_id""", binds)
            row = _row(cursor)
            if row is None:
                raise _not_found()
            detail = {
                "job": self._summary(row),
                "extraction": _stored_json(row["payload_json"]),
                "validation": _stored_json(row["validation_json"], {"issues": []}),
                "processing_metadata": safe_processing_metadata(_stored_json(row["processing_metadata_json"], {})),
                "review_history": [],
            }
            cursor.execute(f"""SELECT r.review_id AS id, r.expected_version AS from_version,
                    r.resulting_version AS to_version, r.decision, r.review_comment AS "COMMENT",
                    r.reviewer_id AS actor, SYS_EXTRACT_UTC(r.reviewed_at) AS created_at
                FROM {SCHEMA}.kk_reviews r JOIN {SCHEMA}.kk_jobs j ON j.job_id=r.job_id
                WHERE {self._scope} AND j.job_id=:job_id
                ORDER BY r.resulting_version, r.review_id""", binds)
            for review in _rows(cursor):
                review["from_version"] = int(review["from_version"])
                review["to_version"] = int(review["to_version"])
                review["comment"] = _text(review["comment"]) or ""
                review["created_at"] = _timestamp(review["created_at"])
                detail["review_history"].append(review)
            if full_history and detail["review_history"]:
                cursor.execute(f"""SELECT e.job_version, e.payload_json, e.validation_json
                    FROM {SCHEMA}.kk_extractions e JOIN {SCHEMA}.kk_jobs j ON j.job_id=e.job_id
                    WHERE {self._scope} AND j.job_id=:job_id AND e.job_version<=j.version
                    ORDER BY e.job_version""", binds)
                snapshots = {}
                for snapshot in _rows(cursor):
                    snapshots[int(snapshot["job_version"])] = (
                        _stored_json(snapshot["payload_json"]), _stored_json(snapshot["validation_json"]),
                    )
                for review in detail["review_history"]:
                    prior = [version for version in snapshots if version <= review["from_version"]]
                    latest = snapshots.get(review["to_version"])
                    if not prior or latest is None:
                        raise DomainError("STORED_RESULT_INVALID", "The stored review history is incomplete.", 500)
                    review["previous_extraction"] = snapshots[max(prior)][0]
                    review["extraction"], review["validation"] = latest
            return detail

    def list_jobs(self, status=None, search=None):
        if status is not None and status != "" and status not in STATES:
            raise DomainError("INVALID_STATUS", "Unsupported status filter.")
        if search is not None and (not isinstance(search, str) or len(search) > 200 or "\x00" in search):
            raise DomainError("INVALID_QUERY", "Search must be text up to 200 characters.")
        where, binds = [self._scope], dict(self._scope_binds)
        if status:
            where.append("j.status=:status")
            binds["status"] = status
        if search:
            # INSTR treats percent, underscores and quotes as literal characters.
            where.append("(INSTR(LOWER(j.case_id),LOWER(:search))>0 OR INSTR(LOWER(d.filename),LOWER(:search))>0 OR INSTR(j.job_id,:search)>0)")
            binds["search"] = search
        with self._session() as connection, connection.cursor() as cursor:
            def list_query(review_columns, review_join):
                return f"""SELECT {_JOB_COLUMNS},
                    CASE WHEN e.extraction_id IS NULL THEN 0 ELSE 1 END AS result_available,
                    CASE WHEN e.extraction_id IS NULL THEN NULL ELSE
                        (SELECT COUNT(*) FROM {SCHEMA}.kk_members m
                         WHERE m.extraction_id=e.extraction_id)
                    END AS member_count, {review_columns}
                {_JOB_FROM}
                {review_join}
                LEFT JOIN {SCHEMA}.kk_extractions e
                    ON e.job_id=j.job_id AND e.job_version=j.current_extraction_version
                WHERE {' AND '.join(where)} ORDER BY j.created_at DESC,j.job_id DESC
                FETCH FIRST 500 ROWS ONLY"""

            try:
                cursor.execute(list_query("nr.open_count AS nik_open_count, nr.first_key AS nik_first_open_key",
                                          _OPEN_NIK_BY_JOB), binds)
            except self.driver.DatabaseError as exc:
                # Installations without the optional NIK review tables can still open their documents.
                if not exc.args or getattr(exc.args[0], "code", None) != 942:
                    raise
                cursor.execute(list_query("NULL AS nik_open_count, NULL AS nik_first_open_key", ""), binds)
            jobs = []
            for row in _rows(cursor):
                summary = self._summary(row)
                # Only counts and availability leave the list endpoint. Detail,
                # export and review retain their existing payload contracts.
                summary["result_available"] = bool(int(row["result_available"]))
                summary["member_count"] = (int(row["member_count"])
                                           if row["member_count"] is not None else None)
                summary["nik_open_count"] = (int(row["nik_open_count"])
                                             if row["nik_open_count"] is not None else None)
                summary["nik_first_open_key"] = row["nik_first_open_key"]
                jobs.append(summary)
            return jobs

    def processing_status(self):
        """Read current-scope capacity without reserving an attempt.

        This does not certify worker health or promise that a queued job will
        finish. Upload persistence is independent of processing capacity.
        """
        unknown = {"status": "UNKNOWN", "processing_available": None, "remaining_attempts": None}
        try:
            with self._session() as connection, connection.cursor() as cursor:
                cursor.execute(f"""SELECT max_requests,used_requests
                    FROM {SCHEMA}.kk_cloud_scopes WHERE scope_id=:scope_id""",
                    {"scope_id": self.scope_id})
                row = _row(cursor)
        except DomainError as error:
            if error.code == "DATABASE_UNAVAILABLE":
                # This optional status remains unknown when its read fails;
                # never infer available capacity from a missing response.
                return unknown
            raise
        if row is None:
            return unknown
        try:
            maximum, used = row["max_requests"], row["used_requests"]
            if (type(maximum) is bool or type(used) is bool
                    or maximum != int(maximum) or used != int(used)
                    or not 1 <= int(maximum) <= 10000 or int(used) < 0):
                return unknown
            remaining = max(0, int(maximum) - int(used))
        except (KeyError, TypeError, ValueError, OverflowError):
            return unknown
        return {"status": "AVAILABLE" if remaining else "PAUSED",
                "processing_available": remaining > 0, "remaining_attempts": remaining}

    def get_job(self, job_id):
        job_id = _job_id(job_id)
        with self._session() as connection:
            return self._detail(connection, job_id)

    def image_record(self, job_id):
        """Private proxy inputs only. Never put this record in a UI response."""
        job_id = _job_id(job_id)
        with self._session() as connection, connection.cursor() as cursor:
            cursor.execute(f"""SELECT d.object_name, d.object_version_id, d.sha256,
                    d.mime_type, d.byte_length
                {_JOB_FROM} WHERE {self._scope} AND j.job_id=:job_id
                    AND d.source_kind='OBJECT_STORAGE' AND d.upload_state='READY'""",
                {**self._scope_binds, "job_id": job_id})
            row = _row(cursor)
            if row is None:
                raise _not_found()
            if not row["object_name"] or not row["object_version_id"]:
                raise DomainError("SOURCE_IMAGE_UNAVAILABLE", "The source image is not available.", 409)
            row["sha256"] = row["sha256"].strip()
            row["byte_length"] = int(row["byte_length"])
            return row

    def review_job(self, job_id, expected_version, decision, extraction, comment):
        job_id = _job_id(job_id)
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
        try:
            validation = {"issues": validate_payload(extraction)}
        except PayloadError:
            raise DomainError("INVALID_PAYLOAD", "The extraction does not match the required document schema.", 422) from None
        if decision == "ACCEPTED" and any(issue["severity"] == "ERROR" for issue in validation["issues"]):
            raise DomainError("REVIEW_BLOCKED", "Resolve validation errors and unreviewed fields before acceptance.", 422)
        if (decision == "ACCEPTED" and any(issue["code"] == "LEGACY_IDENTIFIER_REVIEW" for issue in validation["issues"])
                and not comment.strip()):
            raise DomainError("REVIEW_COMMENT_REQUIRED", "Record your source-image check of legacy identifiers in a review comment before acceptance.", 422)
        with self._session(write=True) as connection, connection.cursor() as cursor:
            binds = {**self._scope_binds, "job_id": job_id}
            cursor.execute(f"""SELECT j.status,j.version,j.current_extraction_version
                FROM {SCHEMA}.kk_jobs j WHERE {self._scope} AND j.job_id=:job_id
                FOR UPDATE WAIT 5""", binds)
            row = _row(cursor)
            if row is None:
                raise _not_found()
            if row["version"] != expected_version:
                raise DomainError("VERSION_CONFLICT", "Another update changed this job. Reload before saving your review.", 409)
            if row["status"] not in ("NEEDS_REVIEW", "ACCEPTED") or row["current_extraction_version"] is None:
                raise DomainError("INVALID_STATE", "Only extracted jobs can be reviewed.", 409)
            cursor.execute(f"""SELECT e.processing_metadata_json
                FROM {SCHEMA}.kk_extractions e JOIN {SCHEMA}.kk_jobs j ON j.job_id=e.job_id
                WHERE {self._scope} AND j.job_id=:job_id AND e.job_version=j.current_extraction_version""", binds)
            snapshot = _row(cursor)
            if snapshot is None:
                raise DomainError("STORED_RESULT_INVALID", "The stored result could not be read.", 500)
            metadata = safe_processing_metadata(_stored_json(snapshot["processing_metadata_json"], {}))
            next_version = expected_version + 1
            # The locked, scope-checked parent owns every appended child below.
            # These tables have no independent cloud_scope_id column.
            write_snapshot(cursor, self.driver, extraction_id=uuid.uuid4().hex,
                           job_id=job_id, job_version=next_version, payload=extraction,
                           validation=validation, source_kind="HUMAN_REVIEW", processing_metadata=metadata)
            insert(cursor, self.driver, "kk_reviews", {
                "review_id": uuid.uuid4().hex, "job_id": job_id,
                "expected_version": expected_version, "resulting_version": next_version,
                "decision": decision, "reviewer_id": self.reviewer_identity,
                "review_comment": comment, "source_kind": "LIVE_REVIEW",
            }, ("review_comment",))
            cursor.execute(f"""UPDATE {SCHEMA}.kk_jobs SET status=:decision,version=:next_version,
                    current_extraction_version=:next_version,updated_at=SYSTIMESTAMP
                WHERE {self._scope.replace('j.', '')} AND job_id=:job_id
                    AND version=:expected_version AND status IN ('NEEDS_REVIEW','ACCEPTED')""",
                {**binds, "decision": decision, "next_version": next_version, "expected_version": expected_version})
            if cursor.rowcount != 1:
                raise DomainError("VERSION_CONFLICT", "Another update changed this job. Reload before saving your review.", 409)
            # Read our writes before the single commit; a response-read failure
            # must also roll back the snapshot, review and current-version pointer.
            return self._detail(connection, job_id)

    def export_job(self, job_id):
        job_id = _job_id(job_id)
        with self._session() as connection:
            detail = self._detail(connection, job_id, full_history=True)
            if detail["extraction"] is None:
                raise DomainError("NOT_READY", "This job has no extraction to export.", 409)
            detail["export"] = {
                "schema_version": "1.0", "exported_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                "source_sha256": detail["job"]["sha256"], "job_version": detail["job"]["version"],
                "processing_mode": "oci", "is_ai_extraction": True,
                "human_accepted": detail["job"]["status"] == "ACCEPTED",
            }
            return detail
