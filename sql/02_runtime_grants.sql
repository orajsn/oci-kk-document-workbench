-- Run as KK_OWNER after migration V1. No DELETE, DDL, ANY or PUBLIC grants.
WHENEVER SQLERROR EXIT SQL.SQLCODE ROLLBACK

GRANT SELECT, INSERT ON kk_documents TO kk_runtime_role;
GRANT UPDATE (upload_state, object_namespace, object_bucket, object_name,
              object_version_id, mime_type, byte_length, data_region)
  ON kk_documents TO kk_runtime_role;
GRANT SELECT, INSERT, UPDATE ON kk_jobs TO kk_runtime_role;
GRANT SELECT, INSERT, UPDATE ON kk_outbox TO kk_runtime_role;
GRANT SELECT, INSERT ON kk_extractions TO kk_runtime_role;
GRANT SELECT, INSERT ON kk_households TO kk_runtime_role;
GRANT SELECT, INSERT ON kk_members TO kk_runtime_role;
GRANT SELECT, INSERT ON kk_field_evidence TO kk_runtime_role;
GRANT SELECT, INSERT ON kk_review_flags TO kk_runtime_role;
GRANT SELECT, INSERT ON kk_reviews TO kk_runtime_role;
GRANT SELECT ON kk_current_results TO kk_runtime_role;
GRANT SELECT ON kk_job_health TO kk_runtime_role;

-- The controlled export loader never publishes work or edits existing jobs.
GRANT SELECT ON kk_schema_migrations TO kk_import_role;
GRANT SELECT, INSERT ON kk_documents TO kk_import_role;
GRANT SELECT, INSERT ON kk_jobs TO kk_import_role;
GRANT UPDATE (current_extraction_version) ON kk_jobs TO kk_import_role;
GRANT INSERT ON kk_extractions TO kk_import_role;
GRANT INSERT ON kk_households TO kk_import_role;
GRANT INSERT ON kk_members TO kk_import_role;
GRANT INSERT ON kk_field_evidence TO kk_import_role;
GRANT INSERT ON kk_review_flags TO kk_import_role;
GRANT INSERT ON kk_reviews TO kk_import_role;
GRANT SELECT, INSERT ON kk_import_receipts TO kk_import_role;
