-- Run as KK_OWNER after V2. Runtime gets no configuration/budget-reset UPDATE.
-- This role already has the V1 object grants. No new DELETE, DDL or ANY grant.
WHENEVER SQLERROR EXIT SQL.SQLCODE ROLLBACK

GRANT SELECT ON kk_schema_migrations TO kk_runtime_role;
GRANT SELECT, INSERT ON kk_cloud_scopes TO kk_runtime_role;
GRANT UPDATE (used_requests, updated_at) ON kk_cloud_scopes TO kk_runtime_role;
GRANT SELECT ON kk_current_results TO kk_runtime_role;

-- Runtime INSERT creates only the scope admitted by the CLI approval config.
-- These application grants do not replace operator authentication or OCI IAM.
-- A scope ceiling/config change requires a separately approved migration/DBA
-- operation or a separately approved scope, never an implicit counter reset.
