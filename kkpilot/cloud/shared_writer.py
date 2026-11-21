"""Bind-only snapshot projection helpers; no connection, SDK import or commit."""
from __future__ import annotations

from datetime import datetime
import json
import re

from ..validation import HOUSEHOLD_FIELDS, MEMBER_FIELDS, assert_valid_payload

SCHEMA = "KK_OWNER"
TABLES = frozenset({
    "kk_documents", "kk_jobs", "kk_outbox", "kk_cloud_scopes", "kk_extractions",
    "kk_households", "kk_members", "kk_field_evidence", "kk_review_flags",
    "kk_reviews", "kk_import_receipts",
})


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def insert(cursor, driver, table: str, values: dict, clobs: tuple = ()) -> None:
    """Identifiers are internal literals; every application value is bound."""
    if table not in TABLES or any(not re.fullmatch(r"[a-z_][a-z0-9_]*", name) for name in values):
        raise ValueError("Unexpected internal SQL identifier.")
    sizes = {name: driver.DB_TYPE_CLOB for name in clobs}
    sizes.update({name: driver.DB_TYPE_TIMESTAMP_TZ for name, value in values.items()
                  if isinstance(value, datetime)})
    cursor.setinputsizes(**sizes)
    columns = ",".join(values)
    binds = ",".join(":" + name for name in values)
    cursor.execute(f"INSERT INTO {SCHEMA}.{table} ({columns}) VALUES ({binds})", values)


def write_snapshot(cursor, driver, *, extraction_id: str, job_id: str, job_version: int,
                   payload: dict, validation: dict, source_kind: str,
                   processing_metadata: dict | None = None) -> None:
    """Write one immutable payload plus all projections in the caller's transaction.

    V2 is required when processing_metadata is supplied. This function never
    decides acceptance, advances a job pointer, commits or acknowledges a queue.
    """
    assert_valid_payload(payload)
    values = {
        "extraction_id": extraction_id, "job_id": job_id, "job_version": job_version,
        "schema_version": payload["schema_version"], "layout_family": payload["layout_family"],
        "source_kind": source_kind, "payload_json": canonical(payload),
        "validation_json": canonical(validation),
    }
    clobs = ("payload_json", "validation_json")
    if processing_metadata is not None:
        values["processing_metadata_json"] = canonical(processing_metadata)
        clobs += ("processing_metadata_json",)
    insert(cursor, driver, "kk_extractions", values, clobs)
    insert(cursor, driver, "kk_households", {"extraction_id": extraction_id, **payload["household"]}, HOUSEHOLD_FIELDS)
    for member in payload["members"]:
        insert(cursor, driver, "kk_members", {"extraction_id": extraction_id, **member},
               tuple(field for field in MEMBER_FIELDS if field != "row_number"))
    for ordinal, item in enumerate(payload["field_evidence"], 1):
        insert(cursor, driver, "kk_field_evidence", {
            "extraction_id": extraction_id, "ordinal": ordinal, "field_path": item["path"],
            "status": item["status"], "section": item["section"],
            "row_number": item.get("row_number"), "note": item.get("note"),
        }, ("note",))
    for ordinal, item in enumerate(payload["review_flags"], 1):
        insert(cursor, driver, "kk_review_flags", {
            "extraction_id": extraction_id, "ordinal": ordinal,
            "field_path": item["path"], "code": item["code"], "message": item["message"],
        }, ("field_path", "code", "message"))
