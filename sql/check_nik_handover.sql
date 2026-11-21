-- SQLcl or SQL*Plus. Read-only. Paste the new job ID from the operator workspace.
-- Example: @sql/check_nik_handover.sql <job_id>
WHENEVER SQLERROR EXIT SQL.SQLCODE
SET VERIFY OFF
SELECT j.case_id, j.status, j.version, e.job_version AS model_version,
       r.member_count, r.flagged, r.cleared, r.review_count, r.finished_at
  FROM kk_owner.kk_jobs j
  LEFT JOIN kk_owner.kk_extractions e ON e.job_id = j.job_id AND e.source_kind = 'MODEL'
  LEFT JOIN kk_owner.kk_nik_route_runs r ON r.job_id = j.job_id
 WHERE j.job_id = '&1'
 ORDER BY e.job_version;

SELECT c.member_row, c.original_job_version, c.topic, ev.revision, ev.action, ev.decided_at
  FROM kk_owner.kk_nik_review_cases c
  LEFT JOIN kk_owner.kk_nik_review_events ev ON ev.field_key = c.field_key
 WHERE c.job_id = '&1'
 ORDER BY c.member_row, ev.revision;
