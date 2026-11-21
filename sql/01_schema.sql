-- Migration V1. Run ONCE as KK_OWNER after 00_account_grants.sql.
-- Oracle DDL commits implicitly. Stop on the first error; reconcile partial DDL
-- using USER_OBJECTS and the release manifest. Never drop/recreate to rerun.
-- Compatible textual JSON: CLOB IS JSON, not a version-dependent native JSON type.
WHENEVER SQLERROR EXIT SQL.SQLCODE ROLLBACK
SET ECHO OFF
SET VERIFY OFF

BEGIN
  IF SYS_CONTEXT('USERENV', 'SESSION_USER') <> 'KK_OWNER' THEN
    RAISE_APPLICATION_ERROR(-20001, 'Run this migration as KK_OWNER only');
  END IF;
END;
/

CREATE TABLE kk_schema_migrations (
  migration_version VARCHAR2(30 CHAR) PRIMARY KEY,
  description VARCHAR2(500 CHAR) NOT NULL,
  installed_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
  installed_by VARCHAR2(128 CHAR) DEFAULT USER NOT NULL
);

CREATE TABLE kk_documents (
  document_id VARCHAR2(36 CHAR) PRIMARY KEY,
  case_id VARCHAR2(128 CHAR) NOT NULL,
  sha256 CHAR(64 CHAR) NOT NULL,
  filename VARCHAR2(512 CHAR) NOT NULL,
  mime_type VARCHAR2(100 CHAR),
  byte_length NUMBER(19,0),
  source_kind VARCHAR2(20 CHAR) NOT NULL,
  object_namespace VARCHAR2(256 CHAR),
  object_bucket VARCHAR2(256 CHAR),
  object_name VARCHAR2(1024 CHAR),
  object_version_id VARCHAR2(256 CHAR),
  data_region VARCHAR2(80 CHAR) NOT NULL,
  upload_state VARCHAR2(20 CHAR) DEFAULT 'READY' NOT NULL,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
  CONSTRAINT kk_doc_case_uq UNIQUE (document_id, case_id),
  CONSTRAINT kk_doc_bytes_ck CHECK (byte_length > 0),
  CONSTRAINT kk_doc_source_ck CHECK (source_kind IN ('OBJECT_STORAGE','LOCAL_EXPORT')),
  CONSTRAINT kk_doc_state_ck CHECK (upload_state IN ('UPLOADING','READY','QUARANTINED')),
  CONSTRAINT kk_doc_object_ck CHECK (source_kind <> 'OBJECT_STORAGE' OR
    (object_namespace IS NOT NULL AND object_bucket IS NOT NULL AND object_name IS NOT NULL
     AND mime_type IS NOT NULL AND byte_length IS NOT NULL))
);
CREATE INDEX kk_doc_case_hash_ix ON kk_documents (case_id, sha256);

CREATE TABLE kk_jobs (
  job_id VARCHAR2(36 CHAR) PRIMARY KEY,
  document_id VARCHAR2(36 CHAR) NOT NULL,
  case_id VARCHAR2(128 CHAR) NOT NULL,
  idempotency_key VARCHAR2(128 CHAR) NOT NULL,
  status VARCHAR2(20 CHAR) DEFAULT 'QUEUED' NOT NULL,
  processing_mode VARCHAR2(20 CHAR) NOT NULL,
  schema_version VARCHAR2(30 CHAR) NOT NULL,
  prompt_version VARCHAR2(100 CHAR),
  model_id VARCHAR2(512 CHAR),
  model_region VARCHAR2(80 CHAR),
  routing_profile_id VARCHAR2(512 CHAR),
  current_extraction_version NUMBER(10,0),
  version NUMBER(10,0) DEFAULT 1 NOT NULL,
  attempt_count NUMBER(10,0) DEFAULT 0 NOT NULL,
  max_attempts NUMBER(10,0) DEFAULT 3 NOT NULL,
  lease_owner VARCHAR2(128 CHAR),
  lease_token VARCHAR2(36 CHAR),
  lease_until TIMESTAMP WITH TIME ZONE,
  available_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
  completed_at TIMESTAMP WITH TIME ZONE,
  error_code VARCHAR2(100 CHAR),
  CONSTRAINT kk_job_doc_fk FOREIGN KEY (document_id, case_id)
    REFERENCES kk_documents (document_id, case_id),
  CONSTRAINT kk_job_idem_uq UNIQUE (case_id, idempotency_key),
  CONSTRAINT kk_job_status_ck CHECK (status IN
    ('QUEUED','PROCESSING','NEEDS_REVIEW','ACCEPTED','FAILED')),
  CONSTRAINT kk_job_mode_ck CHECK (processing_mode IN ('demo','reference','oci')),
  CONSTRAINT kk_job_version_ck CHECK (version >= 1),
  CONSTRAINT kk_job_attempts_ck CHECK (attempt_count >= 0 AND max_attempts >= 1),
  CONSTRAINT kk_job_extract_ck CHECK
    (current_extraction_version IS NULL OR current_extraction_version <= version),
  CONSTRAINT kk_job_lease_ck CHECK
    ((lease_owner IS NULL AND lease_token IS NULL AND lease_until IS NULL) OR
     (lease_owner IS NOT NULL AND lease_token IS NOT NULL AND lease_until IS NOT NULL))
);
CREATE INDEX kk_job_work_ix ON kk_jobs (status, available_at, created_at);
CREATE INDEX kk_job_lease_ix ON kk_jobs (status, lease_until);
CREATE INDEX kk_job_doc_ix ON kk_jobs (document_id, case_id);

-- Transactional outbox: only opaque job/case IDs and immutable object references.
-- Never include images, extracted names, NIKs, KK numbers or credentials here.
CREATE TABLE kk_outbox (
  outbox_id VARCHAR2(36 CHAR) PRIMARY KEY,
  job_id VARCHAR2(36 CHAR) NOT NULL REFERENCES kk_jobs (job_id),
  event_type VARCHAR2(30 CHAR) NOT NULL,
  event_version NUMBER(10,0) NOT NULL,
  payload_json CLOB NOT NULL,
  status VARCHAR2(20 CHAR) DEFAULT 'PENDING' NOT NULL,
  attempt_count NUMBER(10,0) DEFAULT 0 NOT NULL,
  available_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
  lease_owner VARCHAR2(128 CHAR),
  lease_token VARCHAR2(36 CHAR),
  lease_until TIMESTAMP WITH TIME ZONE,
  published_at TIMESTAMP WITH TIME ZONE,
  queue_message_id VARCHAR2(256 CHAR),
  created_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
  CONSTRAINT kk_outbox_event_uq UNIQUE (job_id, event_type, event_version),
  CONSTRAINT kk_outbox_json_ck CHECK (payload_json IS JSON STRICT WITH UNIQUE KEYS),
  CONSTRAINT kk_outbox_status_ck CHECK (status IN ('PENDING','PUBLISHING','PUBLISHED','FAILED')),
  CONSTRAINT kk_outbox_attempt_ck CHECK (attempt_count >= 0 AND event_version >= 1),
  CONSTRAINT kk_outbox_lease_ck CHECK
    ((lease_owner IS NULL AND lease_token IS NULL AND lease_until IS NULL) OR
     (lease_owner IS NOT NULL AND lease_token IS NOT NULL AND lease_until IS NOT NULL))
);
CREATE INDEX kk_outbox_work_ix ON kk_outbox (status, available_at, created_at);

-- Immutable candidate/review snapshots. Valid JSON is NOT an accuracy decision.
CREATE TABLE kk_extractions (
  extraction_id VARCHAR2(36 CHAR) PRIMARY KEY,
  job_id VARCHAR2(36 CHAR) NOT NULL REFERENCES kk_jobs (job_id),
  job_version NUMBER(10,0) NOT NULL,
  schema_version VARCHAR2(30 CHAR) NOT NULL,
  layout_family VARCHAR2(20 CHAR) NOT NULL,
  source_kind VARCHAR2(30 CHAR) NOT NULL,
  payload_json CLOB NOT NULL,
  validation_json CLOB NOT NULL,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
  CONSTRAINT kk_extract_job_uq UNIQUE (job_id, job_version),
  CONSTRAINT kk_extract_version_ck CHECK (job_version >= 1),
  CONSTRAINT kk_extract_layout_ck CHECK (layout_family IN ('modern','legacy','unknown')),
  CONSTRAINT kk_extract_source_ck CHECK
    (source_kind IN ('MODEL','REFERENCE','DEMO','HUMAN_REVIEW','IMPORTED_EXPORT')),
  CONSTRAINT kk_extract_json_ck CHECK (payload_json IS JSON STRICT WITH UNIQUE KEYS),
  CONSTRAINT kk_extract_val_ck CHECK (validation_json IS JSON STRICT WITH UNIQUE KEYS)
);
ALTER TABLE kk_jobs ADD CONSTRAINT kk_job_current_fk
  FOREIGN KEY (job_id, current_extraction_version)
  REFERENCES kk_extractions (job_id, job_version);

CREATE TABLE kk_households (
  extraction_id VARCHAR2(36 CHAR) PRIMARY KEY REFERENCES kk_extractions (extraction_id),
  kk_number_raw CLOB,
  form_serial CLOB,
  head_name CLOB,
  address CLOB,
  rt CLOB,
  rw CLOB,
  village CLOB,
  district CLOB,
  regency_city CLOB,
  province CLOB,
  postal_code CLOB,
  issued_date_raw CLOB
);

CREATE TABLE kk_members (
  extraction_id VARCHAR2(36 CHAR) NOT NULL REFERENCES kk_extractions (extraction_id),
  row_number NUMBER(10,0) NOT NULL,
  full_name CLOB,
  nik_raw CLOB,
  sex CLOB,
  birth_place CLOB,
  birth_date_raw CLOB,
  religion CLOB,
  education CLOB,
  occupation CLOB,
  blood_group CLOB,
  marital_status CLOB,
  marriage_date_raw CLOB,
  relationship_to_head CLOB,
  nationality CLOB,
  passport_number CLOB,
  residence_permit_number CLOB,
  father_name CLOB,
  mother_name CLOB,
  combined_parent_name CLOB,
  CONSTRAINT kk_member_pk PRIMARY KEY (extraction_id, row_number),
  CONSTRAINT kk_member_row_ck CHECK (row_number > 0)
);
-- No unique NIK/KK constraint; an identifier may recur in different cases/versions.

CREATE TABLE kk_field_evidence (
  extraction_id VARCHAR2(36 CHAR) NOT NULL REFERENCES kk_extractions (extraction_id),
  ordinal NUMBER(10,0) NOT NULL,
  field_path VARCHAR2(512 CHAR) NOT NULL,
  status VARCHAR2(30 CHAR) NOT NULL,
  section VARCHAR2(128 CHAR),
  row_number NUMBER(10,0),
  note CLOB,
  CONSTRAINT kk_evidence_pk PRIMARY KEY (extraction_id, ordinal),
  CONSTRAINT kk_evidence_row_fk FOREIGN KEY (extraction_id, row_number)
    REFERENCES kk_members (extraction_id, row_number),
  CONSTRAINT kk_evidence_ord_ck CHECK (ordinal > 0),
  CONSTRAINT kk_evidence_status_ck CHECK (status IN
    ('READABLE','BLANK_ON_DOCUMENT','UNREADABLE','CROPPED',
     'NOT_PRESENT_IN_LAYOUT','AMBIGUOUS','NOT_REVIEWED'))
);

CREATE TABLE kk_review_flags (
  extraction_id VARCHAR2(36 CHAR) NOT NULL REFERENCES kk_extractions (extraction_id),
  ordinal NUMBER(10,0) NOT NULL,
  field_path CLOB,
  code CLOB,
  message CLOB,
  CONSTRAINT kk_flags_pk PRIMARY KEY (extraction_id, ordinal),
  CONSTRAINT kk_flags_ord_ck CHECK (ordinal > 0)
);

-- Append-only reviewer decisions. Preserve the full original history in import
-- receipts too. Do not log the comment (it can contain personal information).
CREATE TABLE kk_reviews (
  review_id VARCHAR2(36 CHAR) PRIMARY KEY,
  job_id VARCHAR2(36 CHAR) NOT NULL REFERENCES kk_jobs (job_id),
  expected_version NUMBER(10,0) NOT NULL,
  resulting_version NUMBER(10,0) NOT NULL,
  decision VARCHAR2(20 CHAR) NOT NULL,
  reviewer_id VARCHAR2(256 CHAR) NOT NULL,
  review_comment CLOB,
  reviewed_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
  source_kind VARCHAR2(20 CHAR) NOT NULL,
  CONSTRAINT kk_review_version_uq UNIQUE (job_id, resulting_version),
  CONSTRAINT kk_review_versions_ck CHECK
    (expected_version >= 1 AND resulting_version > expected_version),
  CONSTRAINT kk_review_decision_ck CHECK (decision IN ('ACCEPTED','NEEDS_REVIEW')),
  CONSTRAINT kk_review_source_ck CHECK (source_kind IN ('LIVE_REVIEW','IMPORTED_EXPORT'))
);

CREATE TABLE kk_import_receipts (
  export_sha256 CHAR(64 CHAR) PRIMARY KEY,
  job_id VARCHAR2(36 CHAR) NOT NULL REFERENCES kk_jobs (job_id),
  export_json CLOB NOT NULL,
  imported_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
  imported_by VARCHAR2(128 CHAR) DEFAULT USER NOT NULL,
  CONSTRAINT kk_import_json_ck CHECK (export_json IS JSON STRICT WITH UNIQUE KEYS)
);
CREATE INDEX kk_import_job_ix ON kk_import_receipts (job_id);

CREATE OR REPLACE VIEW kk_current_results AS
SELECT j.job_id, j.case_id, j.status, j.processing_mode, j.version,
       j.document_id, e.extraction_id, e.schema_version, e.layout_family,
       e.payload_json, e.validation_json, e.created_at AS snapshot_stored_at
FROM kk_jobs j JOIN kk_extractions e
  ON e.job_id = j.job_id AND e.job_version = j.current_extraction_version;

CREATE OR REPLACE VIEW kk_job_health AS
SELECT status, processing_mode, COUNT(*) AS job_count,
       MIN(created_at) AS earliest_created_at,
       MAX(updated_at) AS latest_updated_at
FROM kk_jobs GROUP BY status, processing_mode;

INSERT INTO kk_schema_migrations (migration_version, description)
VALUES ('1', 'KK immutable extraction snapshots, durable jobs and outbox');
COMMIT;
