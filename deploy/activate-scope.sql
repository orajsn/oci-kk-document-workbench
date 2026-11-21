-- Execute as KK_OWNER with the same scope_id as cloud.json.
-- SQLcl: @deploy/activate-scope.sql my-approved-pilot
WHENEVER SQLERROR EXIT SQL.SQLCODE ROLLBACK
INSERT INTO kk_nik_route_control(scope_id)
SELECT '&1' FROM dual WHERE NOT EXISTS (SELECT 1 FROM kk_nik_route_control WHERE scope_id='&1');
COMMIT;
