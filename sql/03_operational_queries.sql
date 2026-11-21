-- Read-only; summaries deliberately exclude KK/NIK/name/address/image/comment.
SELECT migration_version, installed_at FROM kk_schema_migrations;
SELECT status, processing_mode, job_count, earliest_created_at, latest_updated_at
FROM kk_job_health ORDER BY status, processing_mode;

SELECT status, COUNT(*) AS event_count, MIN(created_at) AS oldest_event
FROM kk_outbox GROUP BY status ORDER BY status;

SELECT COUNT(*) AS expired_processing_leases
FROM kk_jobs WHERE status = 'PROCESSING' AND lease_until < SYSTIMESTAMP;

SELECT COUNT(*) AS results_missing_for_final_jobs
FROM kk_jobs WHERE status IN ('ACCEPTED','NEEDS_REVIEW')
AND current_extraction_version IS NULL;

SELECT COUNT(*) AS ready_jobs_without_initial_outbox
FROM kk_jobs j WHERE j.status = 'QUEUED' AND NOT EXISTS
  (SELECT 1 FROM kk_outbox o WHERE o.job_id = j.job_id);

SELECT COUNT(*) AS orphaned_uploads_for_investigation
FROM kk_documents d WHERE d.upload_state = 'UPLOADING'
  AND d.created_at < SYSTIMESTAMP - INTERVAL '1' HOUR;

SELECT object_name, object_type, status
FROM user_objects WHERE status <> 'VALID' ORDER BY object_type, object_name;
