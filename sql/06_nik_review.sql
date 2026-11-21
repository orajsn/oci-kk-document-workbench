-- Run once as the schema owner after 05_cloud_grants.sql.
WHENEVER SQLERROR EXIT SQL.SQLCODE ROLLBACK
CREATE TABLE KK_OWNER.KK_NIK_REVIEW_CASES (
 field_key VARCHAR2(24 CHAR) PRIMARY KEY,
 job_id VARCHAR2(36 CHAR) NOT NULL REFERENCES KK_OWNER.KK_JOBS(job_id),
 original_job_version NUMBER NOT NULL, source_hash CHAR(64) NOT NULL,
 sample_alias VARCHAR2(12 CHAR) NOT NULL, member_row NUMBER NOT NULL,
 ai_nik VARCHAR2(80 CHAR) NOT NULL, topic VARCHAR2(64 CHAR) NOT NULL,
 flags_json VARCHAR2(4000 CHAR) NOT NULL CHECK(flags_json IS JSON),
 structural NUMBER(1) NOT NULL CHECK(structural IN (0,1)), legacy NUMBER(1) NOT NULL CHECK(legacy IN (0,1)),
 image_width NUMBER NOT NULL, crop_png BLOB NOT NULL, context_png BLOB NOT NULL,
 CONSTRAINT kk_nik_case_row_uq UNIQUE (job_id,original_job_version,member_row));

CREATE TABLE KK_OWNER.KK_NIK_REVIEW_EVENTS (
 field_key VARCHAR2(24 CHAR) NOT NULL REFERENCES KK_OWNER.KK_NIK_REVIEW_CASES(field_key),
 revision NUMBER(10) NOT NULL CHECK(revision>0), request_id VARCHAR2(36 CHAR) NOT NULL,
 action VARCHAR2(12 CHAR) NOT NULL CHECK(action IN ('confirm','correct','unreadable')),
 corrected_nik VARCHAR2(60 CHAR), operator_name VARCHAR2(256 CHAR) NOT NULL,
 decided_at TIMESTAMP WITH TIME ZONE DEFAULT SYSTIMESTAMP NOT NULL,
 CONSTRAINT kk_nik_event_pk PRIMARY KEY(field_key,revision),
 CONSTRAINT kk_nik_request_uq UNIQUE(field_key,request_id),
 CONSTRAINT kk_nik_value_ck CHECK((action='correct' AND corrected_nik IS NOT NULL)
 OR (action IN ('unreadable','confirm') AND corrected_nik IS NULL)));

GRANT SELECT, INSERT ON KK_OWNER.KK_NIK_REVIEW_CASES TO KK_RUNTIME_ROLE;
GRANT SELECT, INSERT ON KK_OWNER.KK_NIK_REVIEW_EVENTS TO KK_RUNTIME_ROLE;
