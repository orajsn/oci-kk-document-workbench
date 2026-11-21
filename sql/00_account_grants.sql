-- DBA-controlled bootstrap, ONCE, in the intended Autonomous database.
-- PRECONDITION: create KK_OWNER, KK_RUNTIME and KK_IMPORTER using your approved
-- identity/secret workflow. This file intentionally contains no CREATE USER,
-- password, ADMIN login, DBA role or ANY privilege.
-- KK_OWNER: migration only. KK_RUNTIME: future application. KK_IMPORTER: loader.
-- Review the pilot 20 GB owner quota before execution; it is not DB allocation.
WHENEVER SQLERROR EXIT SQL.SQLCODE ROLLBACK
SET ECHO OFF
SET VERIFY OFF

CREATE ROLE kk_runtime_role;
CREATE ROLE kk_import_role;
GRANT CREATE SESSION, CREATE TABLE, CREATE VIEW, CREATE TRIGGER TO kk_owner;
ALTER USER kk_owner QUOTA 20G ON DATA;
GRANT CREATE SESSION TO kk_runtime;
GRANT CREATE SESSION TO kk_importer;
GRANT kk_runtime_role TO kk_runtime;
GRANT kk_import_role TO kk_importer;

-- Lock KK_OWNER between migration windows using the approved DBA workflow.
-- KK_RUNTIME and KK_IMPORTER receive object grants in 02_runtime_grants.sql.
