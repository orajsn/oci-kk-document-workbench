-- Migration V2. Run ONCE as KK_OWNER after V1, before the operator cloud CLI.
-- Additive migration, except a candidate-evidence FK is relaxed deliberately:
-- a model's invalid row reference must be retained and sent for review, not lost.
-- Oracle DDL commits implicitly; reconcile partial execution rather than reset.
WHENEVER SQLERROR EXIT SQL.SQLCODE ROLLBACK
SET ECHO OFF
SET VERIFY OFF

DECLARE
  v_count NUMBER;
BEGIN
  IF SYS_CONTEXT('USERENV', 'SESSION_USER') <> 'KK_OWNER' THEN
    RAISE_APPLICATION_ERROR(-20001, 'Run this migration as KK_OWNER only');
  END IF;
  SELECT COUNT(*) INTO v_count FROM kk_schema_migrations WHERE migration_version = '1';
  IF v_count <> 1 THEN
    RAISE_APPLICATION_ERROR(-20002, 'Migration V1 must be complete first');
  END IF;
END;
/

-- One approved configuration per scope. Runtime can consume attempts but cannot
-- change the approval fingerprint or ceiling, or reset the counter by UPDATE.
-- This is an application attempt ceiling, not an OCI monetary budget cap.
CREATE TABLE kk_cloud_scopes (
  scope_id VARCHAR2(64 CHAR) PRIMARY KEY,
  config_sha256 CHAR(64 CHAR) NOT NULL,
  max_requests NUMBER(10,0) NOT NULL,
  used_requests NUMBER(10,0) DEFAULT 0 NOT NULL,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
  CONSTRAINT kk_scope_max_ck CHECK (max_requests BETWEEN 1 AND 10000),
  CONSTRAINT kk_scope_used_ck CHECK (used_requests >= 0 AND used_requests <= max_requests)
);

-- Prerequisite: the DBA grants CREATE TRIGGER to KK_OWNER for this migration.
-- Column UPDATE alone would allow a direct SQL counter reset. Reject decreases
-- at the database boundary too; the CHECK above enforces the approved ceiling.
CREATE OR REPLACE TRIGGER kk_scope_counter_monotonic
BEFORE UPDATE OF used_requests ON kk_cloud_scopes
FOR EACH ROW
BEGIN
  IF :NEW.used_requests < :OLD.used_requests THEN
    RAISE_APPLICATION_ERROR(-20021, 'Cloud scope attempt counter cannot decrease');
  END IF;
END;
/

ALTER TABLE kk_jobs ADD (
  cloud_scope_id VARCHAR2(64 CHAR),
  request_fingerprint CHAR(64 CHAR)
);
ALTER TABLE kk_jobs ADD CONSTRAINT kk_job_scope_fk
  FOREIGN KEY (cloud_scope_id) REFERENCES kk_cloud_scopes (scope_id);
CREATE INDEX kk_job_scope_ix ON kk_jobs (cloud_scope_id, status, available_at);

ALTER TABLE kk_extractions ADD (processing_metadata_json CLOB);
ALTER TABLE kk_extractions ADD CONSTRAINT kk_extract_meta_ck
  CHECK (processing_metadata_json IS JSON STRICT WITH UNIQUE KEYS);

-- Shared payload validation permits section text up to 200 characters and a
-- provisional evidence row reference that business validation may reject.
-- Preserve its raw row number; the complete JSON remains authoritative.
ALTER TABLE kk_field_evidence MODIFY (section VARCHAR2(200 CHAR));
ALTER TABLE kk_field_evidence DROP CONSTRAINT kk_evidence_row_fk;

CREATE OR REPLACE VIEW kk_current_results AS
SELECT j.job_id, j.case_id, j.status, j.processing_mode, j.version,
       j.document_id, e.extraction_id, e.schema_version, e.layout_family,
       e.payload_json, e.validation_json, e.processing_metadata_json,
       e.created_at AS snapshot_stored_at
FROM kk_jobs j JOIN kk_extractions e
  ON e.job_id = j.job_id AND e.job_version = j.current_extraction_version;

INSERT INTO kk_schema_migrations (migration_version, description)
VALUES ('2', 'Scoped cloud operator workflow, durable attempt ceiling and model trace');
COMMIT;
