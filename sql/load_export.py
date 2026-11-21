"""Validate a local pilot export; optionally insert it into the proposed Oracle schema.

Default: offline validation only, no driver import, connection, credential read or
file write. This is a one-time import, not the application's production adapter.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from datetime import datetime
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kkpilot.validation import HOUSEHOLD_FIELDS, MEMBER_FIELDS, assert_valid_payload, validate_payload
from kkpilot.cloud.shared_writer import write_snapshot

ACK = "I_APPROVE_IMPORT_TO_THE_CONFIGURED_PILOT_DATABASE"
MAX_EXPORT_BYTES = 16 * 1024 * 1024
SCHEMA = "KK_OWNER"
TABLES = frozenset({"kk_documents", "kk_jobs", "kk_extractions", "kk_households",
                    "kk_members", "kk_field_evidence", "kk_review_flags",
                    "kk_reviews", "kk_import_receipts"})


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def unique_object(pairs: list[tuple]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key; export rejected.")
        result[key] = value
    return result


def text(value: object, limit: int, label: str, *, empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > limit or "\x00" in value or (not empty and not value):
        raise ValueError(f"Invalid {label}; export rejected.")
    return value


def integer(value: object, label: str, minimum: int = 1) -> int:
    if type(value) is not int or not minimum <= value <= 2_147_483_647:
        raise ValueError(f"Invalid {label}; export rejected.")
    return value


def review_identity(value: object) -> tuple:
    """Local SQLite reviews use integers; distributed Oracle reviews use UUIDs."""
    if type(value) is int:
        return ("local", integer(value, "review ID"))
    if isinstance(value, str) and re.fullmatch(
        r"(?:[0-9a-fA-F]{32}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})", value
    ):
        return ("uuid", uuid.UUID(value).hex)
    raise ValueError("Invalid review ID; export rejected.")


def timestamp(value: object) -> datetime:
    parsed = datetime.fromisoformat(text(value, 50, "timestamp").replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("Timestamps require a timezone; export rejected.")
    return parsed


def validate_record(record: object) -> dict:
    required = {"job", "extraction", "validation", "review_history", "export"}
    if not isinstance(record, dict) or not required <= set(record) <= required | {"processing_metadata"}:
        raise ValueError("Expected the complete GET /api/jobs/{id}/export envelope.")
    job, metadata = record["job"], record["export"]
    if not isinstance(job, dict) or not isinstance(metadata, dict):
        raise ValueError("Invalid export metadata.")
    job_id = text(job.get("id"), 36, "job ID")
    if not re.fullmatch(r"[0-9a-fA-F-]{32,36}", job_id):
        raise ValueError("Invalid job ID format.")
    uuid.UUID(job_id)
    text(job.get("case_id"), 128, "case ID")
    text(job.get("filename"), 512, "filename")
    sha = text(job.get("sha256"), 64, "source hash")
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise ValueError("Invalid source hash.")
    if job.get("status") not in {"NEEDS_REVIEW", "ACCEPTED"}:
        raise ValueError("Only completed reviewable exports can be imported.")
    if job.get("mode") not in {"demo", "reference", "oci"}:
        raise ValueError("Unsupported processing mode.")
    integer(job.get("version"), "job version")
    integer(job.get("attempts"), "attempt count", 0)
    integer(job.get("max_attempts"), "attempt limit")
    timestamp(job.get("created_at"))
    timestamp(job.get("updated_at"))
    if metadata.get("schema_version") != "1.0" or metadata.get("source_sha256") != sha:
        raise ValueError("Export schema/source provenance does not match.")
    if metadata.get("job_version") != job["version"] or metadata.get("processing_mode") != job["mode"]:
        raise ValueError("Export version/mode provenance does not match.")
    if metadata.get("is_ai_extraction") is not (job["mode"] == "oci"):
        raise ValueError("Export AI/replay provenance does not match the processing mode.")
    if metadata.get("human_accepted") is not (job["status"] == "ACCEPTED"):
        raise ValueError("Export acceptance provenance does not match.")
    timestamp(metadata.get("exported_at"))
    assert_valid_payload(record["extraction"])
    processing = record.get("processing_metadata", {})
    if not isinstance(processing, dict) or len(canonical(processing)) > 131072:
        raise ValueError("Invalid or oversized processing metadata.")
    if not isinstance(record["validation"], dict) or not isinstance(record["validation"].get("issues"), list):
        raise ValueError("Invalid validation envelope.")
    history = record["review_history"]
    if not isinstance(history, list) or len(history) > 1000:
        raise ValueError("Invalid or oversized review history.")
    seen_versions = set()
    seen_review_ids = set()
    for review in history:
        if not isinstance(review, dict):
            raise ValueError("Invalid review history entry.")
        identity = review_identity(review.get("id"))
        if identity in seen_review_ids:
            raise ValueError("Duplicate review ID; export rejected.")
        seen_review_ids.add(identity)
        before = integer(review.get("from_version"), "previous review version")
        after = integer(review.get("to_version"), "resulting review version")
        if after != before + 1 or after > job["version"] or after in seen_versions:
            raise ValueError("Review history has inconsistent versions.")
        seen_versions.add(after)
        if review.get("decision") not in {"ACCEPTED", "NEEDS_REVIEW"}:
            raise ValueError("Invalid review decision.")
        text(review.get("actor"), 256, "review actor")
        text(review.get("comment"), 4000, "review comment", empty=True)
        timestamp(review.get("created_at"))
        assert_valid_payload(review.get("extraction"))
        if review.get("previous_extraction") is not None:
            assert_valid_payload(review["previous_extraction"])
        if not isinstance(review.get("validation"), dict):
            raise ValueError("Invalid review validation.")
    encoded = canonical(record)
    snapshots = {job["version"]: (record["extraction"], record["validation"])}
    for review in history:
        version = review["to_version"]
        value = (review["extraction"], review["validation"])
        if version in snapshots and canonical(snapshots[version]) != canonical(value):
            raise ValueError("Current extraction or validation disagrees with review history.")
        snapshots[version] = value
    if job["status"] == "ACCEPTED":
        latest = max(history, key=lambda review: review["to_version"], default=None)
        if latest is None or latest["to_version"] != job["version"] or latest["decision"] != "ACCEPTED":
            raise ValueError("Accepted export lacks a matching current accepted review.")
        if any(issue.get("severity") == "ERROR" for issue in record["validation"]["issues"]):
            raise ValueError("Accepted export contains validation errors.")
        if any(issue["severity"] == "ERROR" for issue in validate_payload(record["extraction"])):
            raise ValueError("Accepted export fails the current deterministic acceptance checks.")
    return {"record": record, "encoded": encoded,
            "digest": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
            "snapshots": snapshots}


def prepare(path: Path) -> dict:
    if path.stat().st_size > MAX_EXPORT_BYTES:
        raise ValueError("Export exceeds the 16 MiB loader limit.")
    with path.open(encoding="utf-8-sig") as handle:
        record = json.load(handle, object_pairs_hook=unique_object,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Non-finite JSON number.")))
    return validate_record(record)


def stable_id(job_id: str, purpose: str) -> str:
    return str(uuid.uuid5(uuid.UUID(job_id), purpose))


def insert(cursor, driver, table: str, values: dict, clobs: tuple = ()) -> None:
    # Identifiers are internal constants. All export values are bind parameters.
    if table not in TABLES or any(not re.fullmatch(r"[a-z_][a-z0-9_]*", name) for name in values):
        raise ValueError("Unexpected internal SQL identifier.")
    sizes = {name: driver.DB_TYPE_CLOB for name in clobs}
    sizes.update({name: driver.DB_TYPE_TIMESTAMP_TZ for name, value in values.items() if isinstance(value, datetime)})
    cursor.setinputsizes(**sizes)
    columns = ",".join(values)
    binds = ",".join(":" + name for name in values)
    cursor.execute(f"INSERT INTO {SCHEMA}.{table} ({columns}) VALUES ({binds})", values)


def load(connection, driver, prepared: dict) -> str:
    record, digest = prepared["record"], prepared["digest"]
    job = record["job"]
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT migration_version FROM {SCHEMA}.kk_schema_migrations WHERE migration_version='2'")
        if cursor.fetchone() is None:
            raise ValueError("Required Oracle migration V2 is absent.")
        cursor.execute(f"SELECT job_id FROM {SCHEMA}.kk_import_receipts WHERE export_sha256=:digest", digest=digest)
        if cursor.fetchone():
            return "already_imported"
        cursor.execute(f"SELECT job_id FROM {SCHEMA}.kk_jobs WHERE job_id=:job_id", job_id=job["id"])
        if cursor.fetchone():
            raise ValueError("Job already exists. Import is insert-only; reconcile revisions separately.")
        document_id = stable_id(job["id"], "source-document")
        insert(cursor, driver, "kk_documents", {
            "document_id": document_id, "case_id": job["case_id"], "sha256": job["sha256"],
            "filename": job["filename"], "source_kind": "LOCAL_EXPORT",
            "data_region": "local-origin-unverified", "created_at": timestamp(job["created_at"]),
        })
        insert(cursor, driver, "kk_jobs", {
            "job_id": job["id"], "document_id": document_id, "case_id": job["case_id"],
            "idempotency_key": "export:" + job["id"], "status": job["status"], "processing_mode": job["mode"],
            "schema_version": "1.0", "version": job["version"], "attempt_count": job["attempts"],
            "max_attempts": job["max_attempts"], "created_at": timestamp(job["created_at"]),
            "updated_at": timestamp(job["updated_at"]),
        })
        for version, (payload, validation) in sorted(prepared["snapshots"].items()):
            extraction_id = stable_id(job["id"], "extraction:" + str(version))
            write_snapshot(cursor, driver, extraction_id=extraction_id, job_id=job["id"],
                           job_version=version, payload=payload, validation=validation,
                           source_kind="IMPORTED_EXPORT",
                           processing_metadata=record.get("processing_metadata", {}) if version == job["version"] else {})
        for review in record["review_history"]:
            insert(cursor, driver, "kk_reviews", {
                "review_id": stable_id(job["id"], "review:" + str(review["id"])), "job_id": job["id"],
                "expected_version": review["from_version"], "resulting_version": review["to_version"],
                "decision": review["decision"], "reviewer_id": review["actor"],
                "review_comment": review["comment"], "reviewed_at": timestamp(review["created_at"]),
                "source_kind": "IMPORTED_EXPORT",
            }, ("review_comment",))
        cursor.execute(f"UPDATE {SCHEMA}.kk_jobs SET current_extraction_version=:version WHERE job_id=:job_id",
                       version=job["version"], job_id=job["id"])
        insert(cursor, driver, "kk_import_receipts", {
            "export_sha256": digest, "job_id": job["id"], "export_json": prepared["encoded"],
        }, ("export_json",))
    connection.commit()
    return "imported"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export_file", type=Path)
    parser.add_argument("--execute", action="store_true", help="Connect and commit one approved pilot export")
    parser.add_argument("--ack", default="", help="Required exact execution acknowledgement; see README")
    args = parser.parse_args()
    try:
        prepared = prepare(args.export_file)
        if not args.execute:
            print(json.dumps({"action": "offline_validation_only", "writes": 0,
                              "snapshots": len(prepared["snapshots"]),
                              "members_in_current_snapshot": len(prepared["record"]["extraction"]["members"]),
                              "reviews": len(prepared["record"]["review_history"])}))
            return 0
        if args.ack != ACK:
            raise ValueError("Execution requires the exact --ack documented in sql/README.md.")
        # Credentials are read ONLY after explicit execution and acknowledgement.
        import oracledb
        if os.environ.get("KK_DB_USER", "KK_IMPORTER").upper() != "KK_IMPORTER":
            raise ValueError("The controlled importer must use KK_IMPORTER, never ADMIN or the owner.")
        kwargs = dict(user="KK_IMPORTER", password=os.environ["KK_DB_PASSWORD"],
                      dsn=os.environ["KK_DB_DSN"], ssl_server_dn_match=True)
        for env, arg in (("KK_DB_CONFIG_DIR", "config_dir"), ("KK_DB_WALLET_DIR", "wallet_location"),
                         ("KK_DB_WALLET_PASSWORD", "wallet_password")):
            if os.environ.get(env):
                kwargs[arg] = os.environ[env]
        with oracledb.connect(**kwargs) as connection:
            try:
                result = load(connection, oracledb, prepared)
            except Exception:
                connection.rollback()
                raise
        print(json.dumps({"action": result, "jobs": 1}))
        return 0
    except Exception as exc:
        # Driver errors may contain connection metadata; never print their body.
        if isinstance(exc, (ValueError, KeyError, OSError, ImportError)):
            print("Export validation/configuration failed; check the private input and documented prerequisites.", file=sys.stderr)
        else:
            print("Oracle import failed and was rolled back; use approved database diagnostics.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
