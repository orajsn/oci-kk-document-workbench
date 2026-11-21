-- Optional value-preserving display views for reviewed deployments.
-- DDL commits implicitly. Existing views/base objects are never replaced.
WHENEVER SQLERROR EXIT SQL.SQLCODE ROLLBACK
SET DEFINE OFF
SET ECHO OFF
SET VERIFY OFF
DECLARE
  v_count NUMBER;
  v_actual VARCHAR2(32767);
  v_expected VARCHAR2(32767);
  PROCEDURE require(p_ok BOOLEAN, p_message VARCHAR2) IS
  BEGIN IF p_ok IS NULL OR NOT p_ok THEN RAISE_APPLICATION_ERROR(-20040, p_message); END IF; END;
  PROCEDURE assert_or_create(p_name VARCHAR2, p_query VARCHAR2, p_check_only BOOLEAN) IS
    v_count NUMBER;
    v_text VARCHAR2(32767);
    v_state VARCHAR2(10);
    v_read_only VARCHAR2(1);
  BEGIN
    SELECT COUNT(*) INTO v_count FROM dba_objects WHERE owner='KK_OWNER' AND object_name = p_name;
    IF v_count = 0 THEN
      require(NOT p_check_only, 'Completed migration has a missing view: ' || p_name);
      EXECUTE IMMEDIATE 'CREATE VIEW KK_OWNER.' || p_name || ' AS ' || p_query || ' WITH READ ONLY';
    ELSE
      SELECT COUNT(*) INTO v_count FROM dba_objects WHERE owner='KK_OWNER' AND object_name=p_name AND object_type='VIEW';
      require(v_count=1, 'New name collides with a pre-existing object: ' || p_name);
      -- DBA_VIEWS.TEXT is LONG; these bounded view definitions fit PL/SQL VARCHAR2.
      SELECT text, read_only INTO v_text, v_read_only FROM dba_views WHERE owner='KK_OWNER' AND view_name=p_name;
      v_text := REGEXP_REPLACE(TRIM(REPLACE(v_text, CHR(13))), '[[:space:]]+WITH READ ONLY$', '', 1, 0, 'i');
      require(TRIM(v_text)=TRIM(REPLACE(p_query, CHR(13))), 'Existing view definition differs: ' || p_name);
      require(v_read_only='Y', 'Existing format view is not read only: ' || p_name);
      SELECT status INTO v_state FROM dba_objects WHERE owner='KK_OWNER' AND object_name=p_name AND object_type='VIEW';
      require(v_state='VALID', 'Existing view is invalid: ' || p_name);
    END IF;
  END;
BEGIN
  require(SYS_CONTEXT('USERENV','SESSION_USER')='ADMIN', 'Reviewed ADMIN migration session required; do not run as runtime');
  SELECT COUNT(*) INTO v_count FROM session_privs WHERE privilege IN ('CREATE ANY VIEW','GRANT ANY OBJECT PRIVILEGE');
  require(v_count=2, 'Existing ADMIN create-view and object-grant privileges required; no system privilege is added');
  SELECT COUNT(*) INTO v_count FROM dba_users WHERE username='KK_OWNER' AND account_status LIKE 'LOCKED%';
  require(v_count=1, 'KK_OWNER must remain locked throughout this ADMIN migration');
  SELECT COUNT(*) INTO v_count FROM nls_database_parameters WHERE parameter='NLS_CHARACTERSET' AND value='AL32UTF8';
  require(v_count=1, 'Unicode whitespace projection currently requires AL32UTF8; review other character sets first');
  SELECT COUNT(*) INTO v_count FROM KK_OWNER.kk_schema_migrations WHERE migration_version IN ('1','2');
  require(v_count=2, 'Complete baseline migrations 1 and 2 required');
  SELECT COUNT(*) INTO v_count FROM KK_OWNER.kk_schema_migrations WHERE migration_version='3-fmt-1.0.0';
  require(v_count IN (0,1), 'Unexpected format migration state');
  SELECT COUNT(*) INTO v_count FROM dba_tab_privs
  WHERE owner='KK_OWNER' AND grantee='KK_RUNTIME_ROLE' AND privilege='SELECT' AND grantable='NO'
    AND table_name IN ('KK_JOBS','KK_EXTRACTIONS','KK_HOUSEHOLDS','KK_MEMBERS');
  require(v_count=4, 'Existing runtime role must already have SELECT on all four base tables');
  SELECT COUNT(*) INTO v_count FROM dba_tab_privs
  WHERE owner='KK_OWNER' AND table_name IN ('KK_FMT1_CURRENT_HOUSEHOLDS','KK_FMT1_CURRENT_MEMBERS')
    AND NOT (grantee='KK_RUNTIME_ROLE' AND privilege='SELECT' AND grantable='NO' AND hierarchy='NO');
  require(v_count=0, 'Unexpected format-view grants; no grant expansion is allowed');
  SELECT COUNT(*) INTO v_count FROM dba_col_privs
  WHERE owner='KK_OWNER' AND table_name IN ('KK_FMT1_CURRENT_HOUSEHOLDS','KK_FMT1_CURRENT_MEMBERS');
  require(v_count=0, 'Unexpected format-view column grants');
  SELECT COUNT(*) INTO v_count FROM dba_tab_columns WHERE owner='KK_OWNER' AND table_name='KK_JOBS' AND ((column_name='JOB_ID' AND data_type = 'VARCHAR2') OR (column_name='CASE_ID' AND data_type = 'VARCHAR2') OR (column_name='DOCUMENT_ID' AND data_type = 'VARCHAR2') OR (column_name='CLOUD_SCOPE_ID' AND data_type = 'VARCHAR2') OR (column_name='REQUEST_FINGERPRINT' AND data_type = 'CHAR') OR (column_name='STATUS' AND data_type = 'VARCHAR2') OR (column_name='PROCESSING_MODE' AND data_type = 'VARCHAR2') OR (column_name='VERSION' AND data_type = 'NUMBER') OR (column_name='CURRENT_EXTRACTION_VERSION' AND data_type = 'NUMBER'));
  require(v_count=9, 'Required baseline columns/types differ: KK_JOBS');
  SELECT COUNT(*) INTO v_count FROM dba_tab_columns WHERE owner='KK_OWNER' AND table_name='KK_EXTRACTIONS' AND ((column_name='EXTRACTION_ID' AND data_type = 'VARCHAR2') OR (column_name='JOB_ID' AND data_type = 'VARCHAR2') OR (column_name='SOURCE_KIND' AND data_type = 'VARCHAR2') OR (column_name='SCHEMA_VERSION' AND data_type = 'VARCHAR2') OR (column_name='LAYOUT_FAMILY' AND data_type = 'VARCHAR2') OR (column_name='JOB_VERSION' AND data_type = 'NUMBER') OR (column_name='CREATED_AT' AND data_type LIKE 'TIMESTAMP%WITH TIME ZONE'));
  require(v_count=7, 'Required baseline columns/types differ: KK_EXTRACTIONS');
  SELECT COUNT(*) INTO v_count FROM dba_tab_columns WHERE owner='KK_OWNER' AND table_name='KK_HOUSEHOLDS' AND ((column_name='EXTRACTION_ID' AND data_type = 'VARCHAR2') OR (column_name='KK_NUMBER_RAW' AND data_type = 'CLOB') OR (column_name='FORM_SERIAL' AND data_type = 'CLOB') OR (column_name='HEAD_NAME' AND data_type = 'CLOB') OR (column_name='ADDRESS' AND data_type = 'CLOB') OR (column_name='RT' AND data_type = 'CLOB') OR (column_name='RW' AND data_type = 'CLOB') OR (column_name='VILLAGE' AND data_type = 'CLOB') OR (column_name='DISTRICT' AND data_type = 'CLOB') OR (column_name='REGENCY_CITY' AND data_type = 'CLOB') OR (column_name='PROVINCE' AND data_type = 'CLOB') OR (column_name='POSTAL_CODE' AND data_type = 'CLOB') OR (column_name='ISSUED_DATE_RAW' AND data_type = 'CLOB'));
  require(v_count=13, 'Required baseline columns/types differ: KK_HOUSEHOLDS');
  SELECT COUNT(*) INTO v_count FROM dba_tab_columns WHERE owner='KK_OWNER' AND table_name='KK_MEMBERS' AND ((column_name='EXTRACTION_ID' AND data_type = 'VARCHAR2') OR (column_name='ROW_NUMBER' AND data_type = 'NUMBER') OR (column_name='FULL_NAME' AND data_type = 'CLOB') OR (column_name='NIK_RAW' AND data_type = 'CLOB') OR (column_name='SEX' AND data_type = 'CLOB') OR (column_name='BIRTH_PLACE' AND data_type = 'CLOB') OR (column_name='BIRTH_DATE_RAW' AND data_type = 'CLOB') OR (column_name='RELIGION' AND data_type = 'CLOB') OR (column_name='EDUCATION' AND data_type = 'CLOB') OR (column_name='OCCUPATION' AND data_type = 'CLOB') OR (column_name='BLOOD_GROUP' AND data_type = 'CLOB') OR (column_name='MARITAL_STATUS' AND data_type = 'CLOB') OR (column_name='MARRIAGE_DATE_RAW' AND data_type = 'CLOB') OR (column_name='RELATIONSHIP_TO_HEAD' AND data_type = 'CLOB') OR (column_name='NATIONALITY' AND data_type = 'CLOB') OR (column_name='PASSPORT_NUMBER' AND data_type = 'CLOB') OR (column_name='RESIDENCE_PERMIT_NUMBER' AND data_type = 'CLOB') OR (column_name='FATHER_NAME' AND data_type = 'CLOB') OR (column_name='MOTHER_NAME' AND data_type = 'CLOB') OR (column_name='COMBINED_PARENT_NAME' AND data_type = 'CLOB'));
  require(v_count=20, 'Required baseline columns/types differ: KK_MEMBERS');
  v_expected := q'~WITH
raw_rows AS (
  SELECT j.job_id, j.case_id, j.document_id, j.cloud_scope_id,
         j.request_fingerprint, j.status, j.processing_mode,
         j.version AS job_version, j.current_extraction_version,
         e.extraction_id, e.job_version AS extraction_job_version,
         e.source_kind AS extraction_source_kind, e.schema_version,
         e.layout_family, e.created_at AS snapshot_stored_at,
         r.kk_number_raw, r.form_serial, r.head_name, r.address, r.rt, r.rw, r.village, r.district, r.regency_city, r.province, r.postal_code, r.issued_date_raw
  FROM KK_OWNER.kk_jobs j
  JOIN KK_OWNER.kk_extractions e
    ON e.job_id = j.job_id AND e.job_version = j.current_extraction_version
  JOIN KK_OWNER.kk_households r ON r.extraction_id = e.extraction_id
),
bounded AS (
  SELECT raw_rows.*,
         CASE WHEN DBMS_LOB.GETLENGTH(UPPER(TRIM(REGEXP_REPLACE(issued_date_raw, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c')))) <= 40 THEN DBMS_LOB.SUBSTR(UPPER(TRIM(REGEXP_REPLACE(issued_date_raw, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))), 40, 1) END AS issued_date_text
  FROM raw_rows
),
parts AS (
  SELECT bounded.*,
         TO_NUMBER((CASE WHEN REGEXP_LIKE(issued_date_text, '^[0123456789]{4}-[0123456789]{2}-[0123456789]{2}$', 'c') THEN SUBSTR(issued_date_text, 1, 4) WHEN (REGEXP_LIKE(issued_date_text, '^[0123456789]{1,2}-[0123456789]{1,2}-[0123456789]{4}$', 'c') OR REGEXP_LIKE(issued_date_text, '^[0123456789]{1,2}/[0123456789]{1,2}/[0123456789]{4}$', 'c')) OR REGEXP_LIKE(issued_date_text, '^[0123456789]{1,2} (JANUARI|FEBRUARI|MARET|APRIL|MEI|JUNI|JULI|AGUSTUS|SEPTEMBER|OKTOBER|NOVEMBER|DESEMBER) [0123456789]{4}$', 'c') THEN REGEXP_SUBSTR(issued_date_text, '[^-/ ]+', 1, 3, 'c') END) DEFAULT NULL ON CONVERSION ERROR, '9999', 'NLS_NUMERIC_CHARACTERS=''.,''') AS issued_date_y,
         TO_NUMBER((CASE WHEN REGEXP_LIKE(issued_date_text, '^[0123456789]{4}-[0123456789]{2}-[0123456789]{2}$', 'c') THEN SUBSTR(issued_date_text, 6, 2) WHEN (REGEXP_LIKE(issued_date_text, '^[0123456789]{1,2}-[0123456789]{1,2}-[0123456789]{4}$', 'c') OR REGEXP_LIKE(issued_date_text, '^[0123456789]{1,2}/[0123456789]{1,2}/[0123456789]{4}$', 'c')) THEN REGEXP_SUBSTR(issued_date_text, '[^-/ ]+', 1, 2, 'c') WHEN REGEXP_LIKE(issued_date_text, '^[0123456789]{1,2} (JANUARI|FEBRUARI|MARET|APRIL|MEI|JUNI|JULI|AGUSTUS|SEPTEMBER|OKTOBER|NOVEMBER|DESEMBER) [0123456789]{4}$', 'c') THEN CASE REGEXP_SUBSTR(issued_date_text, '[^-/ ]+', 1, 2, 'c') WHEN 'JANUARI' THEN '1' WHEN 'FEBRUARI' THEN '2' WHEN 'MARET' THEN '3' WHEN 'APRIL' THEN '4' WHEN 'MEI' THEN '5' WHEN 'JUNI' THEN '6' WHEN 'JULI' THEN '7' WHEN 'AGUSTUS' THEN '8' WHEN 'SEPTEMBER' THEN '9' WHEN 'OKTOBER' THEN '10' WHEN 'NOVEMBER' THEN '11' WHEN 'DESEMBER' THEN '12' END END) DEFAULT NULL ON CONVERSION ERROR, '9999', 'NLS_NUMERIC_CHARACTERS=''.,''') AS issued_date_m,
         TO_NUMBER((CASE WHEN REGEXP_LIKE(issued_date_text, '^[0123456789]{4}-[0123456789]{2}-[0123456789]{2}$', 'c') THEN SUBSTR(issued_date_text, 9, 2) WHEN (REGEXP_LIKE(issued_date_text, '^[0123456789]{1,2}-[0123456789]{1,2}-[0123456789]{4}$', 'c') OR REGEXP_LIKE(issued_date_text, '^[0123456789]{1,2}/[0123456789]{1,2}/[0123456789]{4}$', 'c')) OR REGEXP_LIKE(issued_date_text, '^[0123456789]{1,2} (JANUARI|FEBRUARI|MARET|APRIL|MEI|JUNI|JULI|AGUSTUS|SEPTEMBER|OKTOBER|NOVEMBER|DESEMBER) [0123456789]{4}$', 'c') THEN REGEXP_SUBSTR(issued_date_text, '[^-/ ]+', 1, 1, 'c') END) DEFAULT NULL ON CONVERSION ERROR, '9999', 'NLS_NUMERIC_CHARACTERS=''.,''') AS issued_date_d
  FROM bounded
),
checked AS (
  SELECT parts.*,
         CASE WHEN issued_date_y BETWEEN 1 AND 9999 AND issued_date_m BETWEEN 1 AND 12 AND issued_date_d BETWEEN 1 AND (CASE WHEN issued_date_m = 2 THEN CASE WHEN (MOD(issued_date_y, 400) = 0 OR (MOD(issued_date_y, 4) = 0 AND MOD(issued_date_y, 100) <> 0)) THEN 29 ELSE 28 END WHEN issued_date_m IN (4,6,9,11) THEN 30 ELSE 31 END) THEN 1 ELSE 0 END AS issued_date_valid
  FROM parts
)
SELECT job_id,
       case_id,
       document_id,
       cloud_scope_id,
       request_fingerprint,
       status,
       processing_mode,
       job_version,
       current_extraction_version,
       extraction_id,
       extraction_job_version,
       extraction_source_kind,
       schema_version,
       layout_family,
       snapshot_stored_at,
       CAST('1.0.0-db-preview' AS VARCHAR2(30)) AS format_rule_version,
       kk_number_raw,
       CAST(CASE WHEN DBMS_LOB.GETLENGTH(REGEXP_REPLACE(kk_number_raw, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), NULL, 1, 0, 'c')) = 16 AND TRANSLATE(DBMS_LOB.SUBSTR(REGEXP_REPLACE(kk_number_raw, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), NULL, 1, 0, 'c'), 16, 1), 'x0123456789', 'x') IS NULL THEN DBMS_LOB.SUBSTR(REGEXP_REPLACE(kk_number_raw, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), NULL, 1, 0, 'c'), 16, 1) END AS VARCHAR2(16 CHAR)) AS kk_number_canonical,
       CASE WHEN kk_number_raw IS NULL THEN 'NULL_INPUT' WHEN NVL(DBMS_LOB.GETLENGTH(REGEXP_REPLACE(kk_number_raw, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), NULL, 1, 0, 'c')),0) = 0 THEN 'BLANK_INPUT' WHEN DBMS_LOB.GETLENGTH(REGEXP_REPLACE(kk_number_raw, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), NULL, 1, 0, 'c')) = 16 AND TRANSLATE(DBMS_LOB.SUBSTR(REGEXP_REPLACE(kk_number_raw, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), NULL, 1, 0, 'c'), 16, 1), 'x0123456789', 'x') IS NULL THEN 'FORMATTED' ELSE 'IDENTIFIER_FORMAT' END AS kk_number_format_status,
       form_serial,
       UPPER(TRIM(REGEXP_REPLACE(form_serial, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))) AS form_serial_canonical,
       head_name,
       REGEXP_REPLACE(REGEXP_REPLACE(UPPER(TRIM(REGEXP_REPLACE(head_name, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))), '(^|[^[:alnum:]_])([[:alpha:]])\.([[:alnum:]_])', '\1\2. \3', 1, 0, 'c'), '(^|[^[:alnum:]_])([[:alpha:]])\.([[:alnum:]_])', '\1\2. \3', 1, 0, 'c') AS head_name_canonical,
       REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE(UPPER(TRIM(REGEXP_REPLACE(head_name, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))), '(^|[^[:alnum:]_])([[:alpha:]])\.([[:alnum:]_])', '\1\2. \3', 1, 0, 'c'), '(^|[^[:alnum:]_])([[:alpha:]])\.([[:alnum:]_])', '\1\2. \3', 1, 0, 'c'), ' ', NULL, 1, 0, 'c') AS head_name_search_key,
       address,
       UPPER(TRIM(REGEXP_REPLACE(address, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))) AS address_canonical,
       rt,
       UPPER(TRIM(REGEXP_REPLACE(rt, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))) AS rt_canonical,
       rw,
       UPPER(TRIM(REGEXP_REPLACE(rw, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))) AS rw_canonical,
       village,
       UPPER(TRIM(REGEXP_REPLACE(village, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))) AS village_canonical,
       district,
       UPPER(TRIM(REGEXP_REPLACE(district, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))) AS district_canonical,
       regency_city,
       UPPER(TRIM(REGEXP_REPLACE(regency_city, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))) AS regency_city_canonical,
       province,
       UPPER(TRIM(REGEXP_REPLACE(province, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))) AS province_canonical,
       postal_code,
       UPPER(TRIM(REGEXP_REPLACE(postal_code, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))) AS postal_code_canonical,
       issued_date_raw,
       CASE WHEN issued_date_valid = 1 THEN TO_DATE((TO_CHAR(issued_date_y, 'FM0000', 'NLS_NUMERIC_CHARACTERS=''.,''') || '-' || TO_CHAR(issued_date_m, 'FM00', 'NLS_NUMERIC_CHARACTERS=''.,''') || '-' || TO_CHAR(issued_date_d, 'FM00', 'NLS_NUMERIC_CHARACTERS=''.,''')), 'FXYYYY-MM-DD', 'NLS_DATE_LANGUAGE=American NLS_CALENDAR=Gregorian') END AS issued_date,
       CASE WHEN issued_date_raw IS NULL THEN 'NULL_INPUT' WHEN NVL(DBMS_LOB.GETLENGTH(UPPER(TRIM(REGEXP_REPLACE(issued_date_raw, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c')))),0) = 0 THEN 'BLANK_INPUT' WHEN issued_date_y IS NULL THEN 'DATE_FORMAT_UNRECOGNIZED' WHEN issued_date_valid = 0 THEN 'INVALID_CALENDAR_DATE' ELSE 'FORMATTED' END AS issued_date_format_status
FROM checked~';
  SELECT COUNT(*) INTO v_count FROM dba_objects WHERE owner='KK_OWNER' AND object_name='KK_FMT1_CURRENT_HOUSEHOLDS';
  IF v_count > 0 THEN assert_or_create('KK_FMT1_CURRENT_HOUSEHOLDS', v_expected, TRUE); END IF;
  v_expected := q'~WITH
raw_rows AS (
  SELECT j.job_id, j.case_id, j.document_id, j.cloud_scope_id,
         j.request_fingerprint, j.status, j.processing_mode,
         j.version AS job_version, j.current_extraction_version,
         e.extraction_id, e.job_version AS extraction_job_version,
         e.source_kind AS extraction_source_kind, e.schema_version,
         e.layout_family, e.created_at AS snapshot_stored_at,
         r.row_number, r.full_name, r.nik_raw, r.sex, r.birth_place, r.birth_date_raw, r.religion, r.education, r.occupation, r.blood_group, r.marital_status, r.marriage_date_raw, r.relationship_to_head, r.nationality, r.passport_number, r.residence_permit_number, r.father_name, r.mother_name, r.combined_parent_name
  FROM KK_OWNER.kk_jobs j
  JOIN KK_OWNER.kk_extractions e
    ON e.job_id = j.job_id AND e.job_version = j.current_extraction_version
  JOIN KK_OWNER.kk_members r ON r.extraction_id = e.extraction_id
),
bounded AS (
  SELECT raw_rows.*,
         CASE WHEN DBMS_LOB.GETLENGTH(UPPER(TRIM(REGEXP_REPLACE(birth_date_raw, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c')))) <= 40 THEN DBMS_LOB.SUBSTR(UPPER(TRIM(REGEXP_REPLACE(birth_date_raw, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))), 40, 1) END AS birth_date_text,
         CASE WHEN DBMS_LOB.GETLENGTH(UPPER(TRIM(REGEXP_REPLACE(marriage_date_raw, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c')))) <= 40 THEN DBMS_LOB.SUBSTR(UPPER(TRIM(REGEXP_REPLACE(marriage_date_raw, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))), 40, 1) END AS marriage_date_text
  FROM raw_rows
),
parts AS (
  SELECT bounded.*,
         TO_NUMBER((CASE WHEN REGEXP_LIKE(birth_date_text, '^[0123456789]{4}-[0123456789]{2}-[0123456789]{2}$', 'c') THEN SUBSTR(birth_date_text, 1, 4) WHEN (REGEXP_LIKE(birth_date_text, '^[0123456789]{1,2}-[0123456789]{1,2}-[0123456789]{4}$', 'c') OR REGEXP_LIKE(birth_date_text, '^[0123456789]{1,2}/[0123456789]{1,2}/[0123456789]{4}$', 'c')) OR REGEXP_LIKE(birth_date_text, '^[0123456789]{1,2} (JANUARI|FEBRUARI|MARET|APRIL|MEI|JUNI|JULI|AGUSTUS|SEPTEMBER|OKTOBER|NOVEMBER|DESEMBER) [0123456789]{4}$', 'c') THEN REGEXP_SUBSTR(birth_date_text, '[^-/ ]+', 1, 3, 'c') END) DEFAULT NULL ON CONVERSION ERROR, '9999', 'NLS_NUMERIC_CHARACTERS=''.,''') AS birth_date_y,
         TO_NUMBER((CASE WHEN REGEXP_LIKE(birth_date_text, '^[0123456789]{4}-[0123456789]{2}-[0123456789]{2}$', 'c') THEN SUBSTR(birth_date_text, 6, 2) WHEN (REGEXP_LIKE(birth_date_text, '^[0123456789]{1,2}-[0123456789]{1,2}-[0123456789]{4}$', 'c') OR REGEXP_LIKE(birth_date_text, '^[0123456789]{1,2}/[0123456789]{1,2}/[0123456789]{4}$', 'c')) THEN REGEXP_SUBSTR(birth_date_text, '[^-/ ]+', 1, 2, 'c') WHEN REGEXP_LIKE(birth_date_text, '^[0123456789]{1,2} (JANUARI|FEBRUARI|MARET|APRIL|MEI|JUNI|JULI|AGUSTUS|SEPTEMBER|OKTOBER|NOVEMBER|DESEMBER) [0123456789]{4}$', 'c') THEN CASE REGEXP_SUBSTR(birth_date_text, '[^-/ ]+', 1, 2, 'c') WHEN 'JANUARI' THEN '1' WHEN 'FEBRUARI' THEN '2' WHEN 'MARET' THEN '3' WHEN 'APRIL' THEN '4' WHEN 'MEI' THEN '5' WHEN 'JUNI' THEN '6' WHEN 'JULI' THEN '7' WHEN 'AGUSTUS' THEN '8' WHEN 'SEPTEMBER' THEN '9' WHEN 'OKTOBER' THEN '10' WHEN 'NOVEMBER' THEN '11' WHEN 'DESEMBER' THEN '12' END END) DEFAULT NULL ON CONVERSION ERROR, '9999', 'NLS_NUMERIC_CHARACTERS=''.,''') AS birth_date_m,
         TO_NUMBER((CASE WHEN REGEXP_LIKE(birth_date_text, '^[0123456789]{4}-[0123456789]{2}-[0123456789]{2}$', 'c') THEN SUBSTR(birth_date_text, 9, 2) WHEN (REGEXP_LIKE(birth_date_text, '^[0123456789]{1,2}-[0123456789]{1,2}-[0123456789]{4}$', 'c') OR REGEXP_LIKE(birth_date_text, '^[0123456789]{1,2}/[0123456789]{1,2}/[0123456789]{4}$', 'c')) OR REGEXP_LIKE(birth_date_text, '^[0123456789]{1,2} (JANUARI|FEBRUARI|MARET|APRIL|MEI|JUNI|JULI|AGUSTUS|SEPTEMBER|OKTOBER|NOVEMBER|DESEMBER) [0123456789]{4}$', 'c') THEN REGEXP_SUBSTR(birth_date_text, '[^-/ ]+', 1, 1, 'c') END) DEFAULT NULL ON CONVERSION ERROR, '9999', 'NLS_NUMERIC_CHARACTERS=''.,''') AS birth_date_d,
         TO_NUMBER((CASE WHEN REGEXP_LIKE(marriage_date_text, '^[0123456789]{4}-[0123456789]{2}-[0123456789]{2}$', 'c') THEN SUBSTR(marriage_date_text, 1, 4) WHEN (REGEXP_LIKE(marriage_date_text, '^[0123456789]{1,2}-[0123456789]{1,2}-[0123456789]{4}$', 'c') OR REGEXP_LIKE(marriage_date_text, '^[0123456789]{1,2}/[0123456789]{1,2}/[0123456789]{4}$', 'c')) OR REGEXP_LIKE(marriage_date_text, '^[0123456789]{1,2} (JANUARI|FEBRUARI|MARET|APRIL|MEI|JUNI|JULI|AGUSTUS|SEPTEMBER|OKTOBER|NOVEMBER|DESEMBER) [0123456789]{4}$', 'c') THEN REGEXP_SUBSTR(marriage_date_text, '[^-/ ]+', 1, 3, 'c') END) DEFAULT NULL ON CONVERSION ERROR, '9999', 'NLS_NUMERIC_CHARACTERS=''.,''') AS marriage_date_y,
         TO_NUMBER((CASE WHEN REGEXP_LIKE(marriage_date_text, '^[0123456789]{4}-[0123456789]{2}-[0123456789]{2}$', 'c') THEN SUBSTR(marriage_date_text, 6, 2) WHEN (REGEXP_LIKE(marriage_date_text, '^[0123456789]{1,2}-[0123456789]{1,2}-[0123456789]{4}$', 'c') OR REGEXP_LIKE(marriage_date_text, '^[0123456789]{1,2}/[0123456789]{1,2}/[0123456789]{4}$', 'c')) THEN REGEXP_SUBSTR(marriage_date_text, '[^-/ ]+', 1, 2, 'c') WHEN REGEXP_LIKE(marriage_date_text, '^[0123456789]{1,2} (JANUARI|FEBRUARI|MARET|APRIL|MEI|JUNI|JULI|AGUSTUS|SEPTEMBER|OKTOBER|NOVEMBER|DESEMBER) [0123456789]{4}$', 'c') THEN CASE REGEXP_SUBSTR(marriage_date_text, '[^-/ ]+', 1, 2, 'c') WHEN 'JANUARI' THEN '1' WHEN 'FEBRUARI' THEN '2' WHEN 'MARET' THEN '3' WHEN 'APRIL' THEN '4' WHEN 'MEI' THEN '5' WHEN 'JUNI' THEN '6' WHEN 'JULI' THEN '7' WHEN 'AGUSTUS' THEN '8' WHEN 'SEPTEMBER' THEN '9' WHEN 'OKTOBER' THEN '10' WHEN 'NOVEMBER' THEN '11' WHEN 'DESEMBER' THEN '12' END END) DEFAULT NULL ON CONVERSION ERROR, '9999', 'NLS_NUMERIC_CHARACTERS=''.,''') AS marriage_date_m,
         TO_NUMBER((CASE WHEN REGEXP_LIKE(marriage_date_text, '^[0123456789]{4}-[0123456789]{2}-[0123456789]{2}$', 'c') THEN SUBSTR(marriage_date_text, 9, 2) WHEN (REGEXP_LIKE(marriage_date_text, '^[0123456789]{1,2}-[0123456789]{1,2}-[0123456789]{4}$', 'c') OR REGEXP_LIKE(marriage_date_text, '^[0123456789]{1,2}/[0123456789]{1,2}/[0123456789]{4}$', 'c')) OR REGEXP_LIKE(marriage_date_text, '^[0123456789]{1,2} (JANUARI|FEBRUARI|MARET|APRIL|MEI|JUNI|JULI|AGUSTUS|SEPTEMBER|OKTOBER|NOVEMBER|DESEMBER) [0123456789]{4}$', 'c') THEN REGEXP_SUBSTR(marriage_date_text, '[^-/ ]+', 1, 1, 'c') END) DEFAULT NULL ON CONVERSION ERROR, '9999', 'NLS_NUMERIC_CHARACTERS=''.,''') AS marriage_date_d
  FROM bounded
),
checked AS (
  SELECT parts.*,
         CASE WHEN birth_date_y BETWEEN 1 AND 9999 AND birth_date_m BETWEEN 1 AND 12 AND birth_date_d BETWEEN 1 AND (CASE WHEN birth_date_m = 2 THEN CASE WHEN (MOD(birth_date_y, 400) = 0 OR (MOD(birth_date_y, 4) = 0 AND MOD(birth_date_y, 100) <> 0)) THEN 29 ELSE 28 END WHEN birth_date_m IN (4,6,9,11) THEN 30 ELSE 31 END) THEN 1 ELSE 0 END AS birth_date_valid,
         CASE WHEN marriage_date_y BETWEEN 1 AND 9999 AND marriage_date_m BETWEEN 1 AND 12 AND marriage_date_d BETWEEN 1 AND (CASE WHEN marriage_date_m = 2 THEN CASE WHEN (MOD(marriage_date_y, 400) = 0 OR (MOD(marriage_date_y, 4) = 0 AND MOD(marriage_date_y, 100) <> 0)) THEN 29 ELSE 28 END WHEN marriage_date_m IN (4,6,9,11) THEN 30 ELSE 31 END) THEN 1 ELSE 0 END AS marriage_date_valid
  FROM parts
)
SELECT job_id,
       case_id,
       document_id,
       cloud_scope_id,
       request_fingerprint,
       status,
       processing_mode,
       job_version,
       current_extraction_version,
       extraction_id,
       extraction_job_version,
       extraction_source_kind,
       schema_version,
       layout_family,
       snapshot_stored_at,
       CAST('1.0.0-db-preview' AS VARCHAR2(30)) AS format_rule_version,
       row_number,
       full_name,
       REGEXP_REPLACE(REGEXP_REPLACE(UPPER(TRIM(REGEXP_REPLACE(full_name, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))), '(^|[^[:alnum:]_])([[:alpha:]])\.([[:alnum:]_])', '\1\2. \3', 1, 0, 'c'), '(^|[^[:alnum:]_])([[:alpha:]])\.([[:alnum:]_])', '\1\2. \3', 1, 0, 'c') AS full_name_canonical,
       REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE(UPPER(TRIM(REGEXP_REPLACE(full_name, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))), '(^|[^[:alnum:]_])([[:alpha:]])\.([[:alnum:]_])', '\1\2. \3', 1, 0, 'c'), '(^|[^[:alnum:]_])([[:alpha:]])\.([[:alnum:]_])', '\1\2. \3', 1, 0, 'c'), ' ', NULL, 1, 0, 'c') AS full_name_search_key,
       nik_raw,
       CAST(CASE WHEN DBMS_LOB.GETLENGTH(REGEXP_REPLACE(nik_raw, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), NULL, 1, 0, 'c')) = 16 AND TRANSLATE(DBMS_LOB.SUBSTR(REGEXP_REPLACE(nik_raw, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), NULL, 1, 0, 'c'), 16, 1), 'x0123456789', 'x') IS NULL THEN DBMS_LOB.SUBSTR(REGEXP_REPLACE(nik_raw, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), NULL, 1, 0, 'c'), 16, 1) END AS VARCHAR2(16 CHAR)) AS nik_canonical,
       CASE WHEN nik_raw IS NULL THEN 'NULL_INPUT' WHEN NVL(DBMS_LOB.GETLENGTH(REGEXP_REPLACE(nik_raw, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), NULL, 1, 0, 'c')),0) = 0 THEN 'BLANK_INPUT' WHEN DBMS_LOB.GETLENGTH(REGEXP_REPLACE(nik_raw, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), NULL, 1, 0, 'c')) = 16 AND TRANSLATE(DBMS_LOB.SUBSTR(REGEXP_REPLACE(nik_raw, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), NULL, 1, 0, 'c'), 16, 1), 'x0123456789', 'x') IS NULL THEN 'FORMATTED' ELSE 'IDENTIFIER_FORMAT' END AS nik_format_status,
       sex,
       UPPER(TRIM(REGEXP_REPLACE(sex, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))) AS sex_canonical,
       birth_place,
       UPPER(TRIM(REGEXP_REPLACE(birth_place, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))) AS birth_place_canonical,
       birth_date_raw,
       CASE WHEN birth_date_valid = 1 THEN TO_DATE((TO_CHAR(birth_date_y, 'FM0000', 'NLS_NUMERIC_CHARACTERS=''.,''') || '-' || TO_CHAR(birth_date_m, 'FM00', 'NLS_NUMERIC_CHARACTERS=''.,''') || '-' || TO_CHAR(birth_date_d, 'FM00', 'NLS_NUMERIC_CHARACTERS=''.,''')), 'FXYYYY-MM-DD', 'NLS_DATE_LANGUAGE=American NLS_CALENDAR=Gregorian') END AS birth_date,
       CASE WHEN birth_date_raw IS NULL THEN 'NULL_INPUT' WHEN NVL(DBMS_LOB.GETLENGTH(UPPER(TRIM(REGEXP_REPLACE(birth_date_raw, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c')))),0) = 0 THEN 'BLANK_INPUT' WHEN birth_date_y IS NULL THEN 'DATE_FORMAT_UNRECOGNIZED' WHEN birth_date_valid = 0 THEN 'INVALID_CALENDAR_DATE' ELSE 'FORMATTED' END AS birth_date_format_status,
       religion,
       UPPER(TRIM(REGEXP_REPLACE(religion, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))) AS religion_canonical,
       education,
       UPPER(TRIM(REGEXP_REPLACE(education, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))) AS education_canonical,
       occupation,
       UPPER(TRIM(REGEXP_REPLACE(occupation, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))) AS occupation_canonical,
       blood_group,
       UPPER(TRIM(REGEXP_REPLACE(blood_group, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))) AS blood_group_canonical,
       marital_status,
       UPPER(TRIM(REGEXP_REPLACE(marital_status, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))) AS marital_status_canonical,
       marriage_date_raw,
       CASE WHEN marriage_date_valid = 1 THEN TO_DATE((TO_CHAR(marriage_date_y, 'FM0000', 'NLS_NUMERIC_CHARACTERS=''.,''') || '-' || TO_CHAR(marriage_date_m, 'FM00', 'NLS_NUMERIC_CHARACTERS=''.,''') || '-' || TO_CHAR(marriage_date_d, 'FM00', 'NLS_NUMERIC_CHARACTERS=''.,''')), 'FXYYYY-MM-DD', 'NLS_DATE_LANGUAGE=American NLS_CALENDAR=Gregorian') END AS marriage_date,
       CASE WHEN marriage_date_raw IS NULL THEN 'NULL_INPUT' WHEN NVL(DBMS_LOB.GETLENGTH(UPPER(TRIM(REGEXP_REPLACE(marriage_date_raw, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c')))),0) = 0 THEN 'BLANK_INPUT' WHEN marriage_date_y IS NULL THEN 'DATE_FORMAT_UNRECOGNIZED' WHEN marriage_date_valid = 0 THEN 'INVALID_CALENDAR_DATE' ELSE 'FORMATTED' END AS marriage_date_format_status,
       relationship_to_head,
       UPPER(TRIM(REGEXP_REPLACE(relationship_to_head, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))) AS relationship_to_head_canonical,
       nationality,
       UPPER(TRIM(REGEXP_REPLACE(nationality, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))) AS nationality_canonical,
       passport_number,
       UPPER(TRIM(REGEXP_REPLACE(passport_number, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))) AS passport_number_canonical,
       residence_permit_number,
       UPPER(TRIM(REGEXP_REPLACE(residence_permit_number, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))) AS residence_permit_number_canonical,
       father_name,
       REGEXP_REPLACE(REGEXP_REPLACE(UPPER(TRIM(REGEXP_REPLACE(father_name, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))), '(^|[^[:alnum:]_])([[:alpha:]])\.([[:alnum:]_])', '\1\2. \3', 1, 0, 'c'), '(^|[^[:alnum:]_])([[:alpha:]])\.([[:alnum:]_])', '\1\2. \3', 1, 0, 'c') AS father_name_canonical,
       REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE(UPPER(TRIM(REGEXP_REPLACE(father_name, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))), '(^|[^[:alnum:]_])([[:alpha:]])\.([[:alnum:]_])', '\1\2. \3', 1, 0, 'c'), '(^|[^[:alnum:]_])([[:alpha:]])\.([[:alnum:]_])', '\1\2. \3', 1, 0, 'c'), ' ', NULL, 1, 0, 'c') AS father_name_search_key,
       mother_name,
       REGEXP_REPLACE(REGEXP_REPLACE(UPPER(TRIM(REGEXP_REPLACE(mother_name, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))), '(^|[^[:alnum:]_])([[:alpha:]])\.([[:alnum:]_])', '\1\2. \3', 1, 0, 'c'), '(^|[^[:alnum:]_])([[:alpha:]])\.([[:alnum:]_])', '\1\2. \3', 1, 0, 'c') AS mother_name_canonical,
       REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE(UPPER(TRIM(REGEXP_REPLACE(mother_name, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))), '(^|[^[:alnum:]_])([[:alpha:]])\.([[:alnum:]_])', '\1\2. \3', 1, 0, 'c'), '(^|[^[:alnum:]_])([[:alpha:]])\.([[:alnum:]_])', '\1\2. \3', 1, 0, 'c'), ' ', NULL, 1, 0, 'c') AS mother_name_search_key,
       combined_parent_name,
       REGEXP_REPLACE(REGEXP_REPLACE(UPPER(TRIM(REGEXP_REPLACE(combined_parent_name, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))), '(^|[^[:alnum:]_])([[:alpha:]])\.([[:alnum:]_])', '\1\2. \3', 1, 0, 'c'), '(^|[^[:alnum:]_])([[:alpha:]])\.([[:alnum:]_])', '\1\2. \3', 1, 0, 'c') AS combined_parent_name_canonical,
       REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE(UPPER(TRIM(REGEXP_REPLACE(combined_parent_name, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))), '(^|[^[:alnum:]_])([[:alpha:]])\.([[:alnum:]_])', '\1\2. \3', 1, 0, 'c'), '(^|[^[:alnum:]_])([[:alpha:]])\.([[:alnum:]_])', '\1\2. \3', 1, 0, 'c'), ' ', NULL, 1, 0, 'c') AS combined_parent_name_search_key
FROM checked~';
  SELECT COUNT(*) INTO v_count FROM dba_objects WHERE owner='KK_OWNER' AND object_name='KK_FMT1_CURRENT_MEMBERS';
  IF v_count > 0 THEN assert_or_create('KK_FMT1_CURRENT_MEMBERS', v_expected, TRUE); END IF;
  v_expected := q'~WITH
raw_rows AS (
  SELECT j.job_id, j.case_id, j.document_id, j.cloud_scope_id,
         j.request_fingerprint, j.status, j.processing_mode,
         j.version AS job_version, j.current_extraction_version,
         e.extraction_id, e.job_version AS extraction_job_version,
         e.source_kind AS extraction_source_kind, e.schema_version,
         e.layout_family, e.created_at AS snapshot_stored_at,
         r.kk_number_raw, r.form_serial, r.head_name, r.address, r.rt, r.rw, r.village, r.district, r.regency_city, r.province, r.postal_code, r.issued_date_raw
  FROM KK_OWNER.kk_jobs j
  JOIN KK_OWNER.kk_extractions e
    ON e.job_id = j.job_id AND e.job_version = j.current_extraction_version
  JOIN KK_OWNER.kk_households r ON r.extraction_id = e.extraction_id
),
bounded AS (
  SELECT raw_rows.*,
         CASE WHEN DBMS_LOB.GETLENGTH(UPPER(TRIM(REGEXP_REPLACE(issued_date_raw, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c')))) <= 40 THEN DBMS_LOB.SUBSTR(UPPER(TRIM(REGEXP_REPLACE(issued_date_raw, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))), 40, 1) END AS issued_date_text
  FROM raw_rows
),
parts AS (
  SELECT bounded.*,
         TO_NUMBER((CASE WHEN REGEXP_LIKE(issued_date_text, '^[0123456789]{4}-[0123456789]{2}-[0123456789]{2}$', 'c') THEN SUBSTR(issued_date_text, 1, 4) WHEN (REGEXP_LIKE(issued_date_text, '^[0123456789]{1,2}-[0123456789]{1,2}-[0123456789]{4}$', 'c') OR REGEXP_LIKE(issued_date_text, '^[0123456789]{1,2}/[0123456789]{1,2}/[0123456789]{4}$', 'c')) OR REGEXP_LIKE(issued_date_text, '^[0123456789]{1,2} (JANUARI|FEBRUARI|MARET|APRIL|MEI|JUNI|JULI|AGUSTUS|SEPTEMBER|OKTOBER|NOVEMBER|DESEMBER) [0123456789]{4}$', 'c') THEN REGEXP_SUBSTR(issued_date_text, '[^-/ ]+', 1, 3, 'c') END) DEFAULT NULL ON CONVERSION ERROR, '9999', 'NLS_NUMERIC_CHARACTERS=''.,''') AS issued_date_y,
         TO_NUMBER((CASE WHEN REGEXP_LIKE(issued_date_text, '^[0123456789]{4}-[0123456789]{2}-[0123456789]{2}$', 'c') THEN SUBSTR(issued_date_text, 6, 2) WHEN (REGEXP_LIKE(issued_date_text, '^[0123456789]{1,2}-[0123456789]{1,2}-[0123456789]{4}$', 'c') OR REGEXP_LIKE(issued_date_text, '^[0123456789]{1,2}/[0123456789]{1,2}/[0123456789]{4}$', 'c')) THEN REGEXP_SUBSTR(issued_date_text, '[^-/ ]+', 1, 2, 'c') WHEN REGEXP_LIKE(issued_date_text, '^[0123456789]{1,2} (JANUARI|FEBRUARI|MARET|APRIL|MEI|JUNI|JULI|AGUSTUS|SEPTEMBER|OKTOBER|NOVEMBER|DESEMBER) [0123456789]{4}$', 'c') THEN CASE REGEXP_SUBSTR(issued_date_text, '[^-/ ]+', 1, 2, 'c') WHEN 'JANUARI' THEN '1' WHEN 'FEBRUARI' THEN '2' WHEN 'MARET' THEN '3' WHEN 'APRIL' THEN '4' WHEN 'MEI' THEN '5' WHEN 'JUNI' THEN '6' WHEN 'JULI' THEN '7' WHEN 'AGUSTUS' THEN '8' WHEN 'SEPTEMBER' THEN '9' WHEN 'OKTOBER' THEN '10' WHEN 'NOVEMBER' THEN '11' WHEN 'DESEMBER' THEN '12' END END) DEFAULT NULL ON CONVERSION ERROR, '9999', 'NLS_NUMERIC_CHARACTERS=''.,''') AS issued_date_m,
         TO_NUMBER((CASE WHEN REGEXP_LIKE(issued_date_text, '^[0123456789]{4}-[0123456789]{2}-[0123456789]{2}$', 'c') THEN SUBSTR(issued_date_text, 9, 2) WHEN (REGEXP_LIKE(issued_date_text, '^[0123456789]{1,2}-[0123456789]{1,2}-[0123456789]{4}$', 'c') OR REGEXP_LIKE(issued_date_text, '^[0123456789]{1,2}/[0123456789]{1,2}/[0123456789]{4}$', 'c')) OR REGEXP_LIKE(issued_date_text, '^[0123456789]{1,2} (JANUARI|FEBRUARI|MARET|APRIL|MEI|JUNI|JULI|AGUSTUS|SEPTEMBER|OKTOBER|NOVEMBER|DESEMBER) [0123456789]{4}$', 'c') THEN REGEXP_SUBSTR(issued_date_text, '[^-/ ]+', 1, 1, 'c') END) DEFAULT NULL ON CONVERSION ERROR, '9999', 'NLS_NUMERIC_CHARACTERS=''.,''') AS issued_date_d
  FROM bounded
),
checked AS (
  SELECT parts.*,
         CASE WHEN issued_date_y BETWEEN 1 AND 9999 AND issued_date_m BETWEEN 1 AND 12 AND issued_date_d BETWEEN 1 AND (CASE WHEN issued_date_m = 2 THEN CASE WHEN (MOD(issued_date_y, 400) = 0 OR (MOD(issued_date_y, 4) = 0 AND MOD(issued_date_y, 100) <> 0)) THEN 29 ELSE 28 END WHEN issued_date_m IN (4,6,9,11) THEN 30 ELSE 31 END) THEN 1 ELSE 0 END AS issued_date_valid
  FROM parts
)
SELECT job_id,
       case_id,
       document_id,
       cloud_scope_id,
       request_fingerprint,
       status,
       processing_mode,
       job_version,
       current_extraction_version,
       extraction_id,
       extraction_job_version,
       extraction_source_kind,
       schema_version,
       layout_family,
       snapshot_stored_at,
       CAST('1.0.0-db-preview' AS VARCHAR2(30)) AS format_rule_version,
       kk_number_raw,
       CAST(CASE WHEN DBMS_LOB.GETLENGTH(REGEXP_REPLACE(kk_number_raw, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), NULL, 1, 0, 'c')) = 16 AND TRANSLATE(DBMS_LOB.SUBSTR(REGEXP_REPLACE(kk_number_raw, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), NULL, 1, 0, 'c'), 16, 1), 'x0123456789', 'x') IS NULL THEN DBMS_LOB.SUBSTR(REGEXP_REPLACE(kk_number_raw, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), NULL, 1, 0, 'c'), 16, 1) END AS VARCHAR2(16 CHAR)) AS kk_number_canonical,
       CASE WHEN kk_number_raw IS NULL THEN 'NULL_INPUT' WHEN NVL(DBMS_LOB.GETLENGTH(REGEXP_REPLACE(kk_number_raw, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), NULL, 1, 0, 'c')),0) = 0 THEN 'BLANK_INPUT' WHEN DBMS_LOB.GETLENGTH(REGEXP_REPLACE(kk_number_raw, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), NULL, 1, 0, 'c')) = 16 AND TRANSLATE(DBMS_LOB.SUBSTR(REGEXP_REPLACE(kk_number_raw, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), NULL, 1, 0, 'c'), 16, 1), 'x0123456789', 'x') IS NULL THEN 'FORMATTED' ELSE 'IDENTIFIER_FORMAT' END AS kk_number_format_status,
       form_serial,
       UPPER(TRIM(REGEXP_REPLACE(form_serial, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))) AS form_serial_canonical,
       head_name,
       REGEXP_REPLACE(REGEXP_REPLACE(UPPER(TRIM(REGEXP_REPLACE(head_name, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))), '(^|[^[:alnum:]_])([[:alpha:]])\.([[:alnum:]_])', '\1\2. \3', 1, 0, 'c'), '(^|[^[:alnum:]_])([[:alpha:]])\.([[:alnum:]_])', '\1\2. \3', 1, 0, 'c') AS head_name_canonical,
       REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE(UPPER(TRIM(REGEXP_REPLACE(head_name, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))), '(^|[^[:alnum:]_])([[:alpha:]])\.([[:alnum:]_])', '\1\2. \3', 1, 0, 'c'), '(^|[^[:alnum:]_])([[:alpha:]])\.([[:alnum:]_])', '\1\2. \3', 1, 0, 'c'), ' ', NULL, 1, 0, 'c') AS head_name_search_key,
       address,
       UPPER(TRIM(REGEXP_REPLACE(address, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))) AS address_canonical,
       rt,
       UPPER(TRIM(REGEXP_REPLACE(rt, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))) AS rt_canonical,
       rw,
       UPPER(TRIM(REGEXP_REPLACE(rw, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))) AS rw_canonical,
       village,
       UPPER(TRIM(REGEXP_REPLACE(village, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))) AS village_canonical,
       district,
       UPPER(TRIM(REGEXP_REPLACE(district, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))) AS district_canonical,
       regency_city,
       UPPER(TRIM(REGEXP_REPLACE(regency_city, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))) AS regency_city_canonical,
       province,
       UPPER(TRIM(REGEXP_REPLACE(province, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))) AS province_canonical,
       postal_code,
       UPPER(TRIM(REGEXP_REPLACE(postal_code, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))) AS postal_code_canonical,
       issued_date_raw,
       CASE WHEN issued_date_valid = 1 THEN TO_DATE((TO_CHAR(issued_date_y, 'FM0000', 'NLS_NUMERIC_CHARACTERS=''.,''') || '-' || TO_CHAR(issued_date_m, 'FM00', 'NLS_NUMERIC_CHARACTERS=''.,''') || '-' || TO_CHAR(issued_date_d, 'FM00', 'NLS_NUMERIC_CHARACTERS=''.,''')), 'FXYYYY-MM-DD', 'NLS_DATE_LANGUAGE=American NLS_CALENDAR=Gregorian') END AS issued_date,
       CASE WHEN issued_date_raw IS NULL THEN 'NULL_INPUT' WHEN NVL(DBMS_LOB.GETLENGTH(UPPER(TRIM(REGEXP_REPLACE(issued_date_raw, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c')))),0) = 0 THEN 'BLANK_INPUT' WHEN issued_date_y IS NULL THEN 'DATE_FORMAT_UNRECOGNIZED' WHEN issued_date_valid = 0 THEN 'INVALID_CALENDAR_DATE' ELSE 'FORMATTED' END AS issued_date_format_status
FROM checked~';
  SELECT COUNT(*) INTO v_count FROM KK_OWNER.kk_schema_migrations WHERE migration_version='3-fmt-1.0.0';
  assert_or_create('KK_FMT1_CURRENT_HOUSEHOLDS', v_expected, v_count=1);
  v_expected := q'~WITH
raw_rows AS (
  SELECT j.job_id, j.case_id, j.document_id, j.cloud_scope_id,
         j.request_fingerprint, j.status, j.processing_mode,
         j.version AS job_version, j.current_extraction_version,
         e.extraction_id, e.job_version AS extraction_job_version,
         e.source_kind AS extraction_source_kind, e.schema_version,
         e.layout_family, e.created_at AS snapshot_stored_at,
         r.row_number, r.full_name, r.nik_raw, r.sex, r.birth_place, r.birth_date_raw, r.religion, r.education, r.occupation, r.blood_group, r.marital_status, r.marriage_date_raw, r.relationship_to_head, r.nationality, r.passport_number, r.residence_permit_number, r.father_name, r.mother_name, r.combined_parent_name
  FROM KK_OWNER.kk_jobs j
  JOIN KK_OWNER.kk_extractions e
    ON e.job_id = j.job_id AND e.job_version = j.current_extraction_version
  JOIN KK_OWNER.kk_members r ON r.extraction_id = e.extraction_id
),
bounded AS (
  SELECT raw_rows.*,
         CASE WHEN DBMS_LOB.GETLENGTH(UPPER(TRIM(REGEXP_REPLACE(birth_date_raw, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c')))) <= 40 THEN DBMS_LOB.SUBSTR(UPPER(TRIM(REGEXP_REPLACE(birth_date_raw, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))), 40, 1) END AS birth_date_text,
         CASE WHEN DBMS_LOB.GETLENGTH(UPPER(TRIM(REGEXP_REPLACE(marriage_date_raw, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c')))) <= 40 THEN DBMS_LOB.SUBSTR(UPPER(TRIM(REGEXP_REPLACE(marriage_date_raw, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))), 40, 1) END AS marriage_date_text
  FROM raw_rows
),
parts AS (
  SELECT bounded.*,
         TO_NUMBER((CASE WHEN REGEXP_LIKE(birth_date_text, '^[0123456789]{4}-[0123456789]{2}-[0123456789]{2}$', 'c') THEN SUBSTR(birth_date_text, 1, 4) WHEN (REGEXP_LIKE(birth_date_text, '^[0123456789]{1,2}-[0123456789]{1,2}-[0123456789]{4}$', 'c') OR REGEXP_LIKE(birth_date_text, '^[0123456789]{1,2}/[0123456789]{1,2}/[0123456789]{4}$', 'c')) OR REGEXP_LIKE(birth_date_text, '^[0123456789]{1,2} (JANUARI|FEBRUARI|MARET|APRIL|MEI|JUNI|JULI|AGUSTUS|SEPTEMBER|OKTOBER|NOVEMBER|DESEMBER) [0123456789]{4}$', 'c') THEN REGEXP_SUBSTR(birth_date_text, '[^-/ ]+', 1, 3, 'c') END) DEFAULT NULL ON CONVERSION ERROR, '9999', 'NLS_NUMERIC_CHARACTERS=''.,''') AS birth_date_y,
         TO_NUMBER((CASE WHEN REGEXP_LIKE(birth_date_text, '^[0123456789]{4}-[0123456789]{2}-[0123456789]{2}$', 'c') THEN SUBSTR(birth_date_text, 6, 2) WHEN (REGEXP_LIKE(birth_date_text, '^[0123456789]{1,2}-[0123456789]{1,2}-[0123456789]{4}$', 'c') OR REGEXP_LIKE(birth_date_text, '^[0123456789]{1,2}/[0123456789]{1,2}/[0123456789]{4}$', 'c')) THEN REGEXP_SUBSTR(birth_date_text, '[^-/ ]+', 1, 2, 'c') WHEN REGEXP_LIKE(birth_date_text, '^[0123456789]{1,2} (JANUARI|FEBRUARI|MARET|APRIL|MEI|JUNI|JULI|AGUSTUS|SEPTEMBER|OKTOBER|NOVEMBER|DESEMBER) [0123456789]{4}$', 'c') THEN CASE REGEXP_SUBSTR(birth_date_text, '[^-/ ]+', 1, 2, 'c') WHEN 'JANUARI' THEN '1' WHEN 'FEBRUARI' THEN '2' WHEN 'MARET' THEN '3' WHEN 'APRIL' THEN '4' WHEN 'MEI' THEN '5' WHEN 'JUNI' THEN '6' WHEN 'JULI' THEN '7' WHEN 'AGUSTUS' THEN '8' WHEN 'SEPTEMBER' THEN '9' WHEN 'OKTOBER' THEN '10' WHEN 'NOVEMBER' THEN '11' WHEN 'DESEMBER' THEN '12' END END) DEFAULT NULL ON CONVERSION ERROR, '9999', 'NLS_NUMERIC_CHARACTERS=''.,''') AS birth_date_m,
         TO_NUMBER((CASE WHEN REGEXP_LIKE(birth_date_text, '^[0123456789]{4}-[0123456789]{2}-[0123456789]{2}$', 'c') THEN SUBSTR(birth_date_text, 9, 2) WHEN (REGEXP_LIKE(birth_date_text, '^[0123456789]{1,2}-[0123456789]{1,2}-[0123456789]{4}$', 'c') OR REGEXP_LIKE(birth_date_text, '^[0123456789]{1,2}/[0123456789]{1,2}/[0123456789]{4}$', 'c')) OR REGEXP_LIKE(birth_date_text, '^[0123456789]{1,2} (JANUARI|FEBRUARI|MARET|APRIL|MEI|JUNI|JULI|AGUSTUS|SEPTEMBER|OKTOBER|NOVEMBER|DESEMBER) [0123456789]{4}$', 'c') THEN REGEXP_SUBSTR(birth_date_text, '[^-/ ]+', 1, 1, 'c') END) DEFAULT NULL ON CONVERSION ERROR, '9999', 'NLS_NUMERIC_CHARACTERS=''.,''') AS birth_date_d,
         TO_NUMBER((CASE WHEN REGEXP_LIKE(marriage_date_text, '^[0123456789]{4}-[0123456789]{2}-[0123456789]{2}$', 'c') THEN SUBSTR(marriage_date_text, 1, 4) WHEN (REGEXP_LIKE(marriage_date_text, '^[0123456789]{1,2}-[0123456789]{1,2}-[0123456789]{4}$', 'c') OR REGEXP_LIKE(marriage_date_text, '^[0123456789]{1,2}/[0123456789]{1,2}/[0123456789]{4}$', 'c')) OR REGEXP_LIKE(marriage_date_text, '^[0123456789]{1,2} (JANUARI|FEBRUARI|MARET|APRIL|MEI|JUNI|JULI|AGUSTUS|SEPTEMBER|OKTOBER|NOVEMBER|DESEMBER) [0123456789]{4}$', 'c') THEN REGEXP_SUBSTR(marriage_date_text, '[^-/ ]+', 1, 3, 'c') END) DEFAULT NULL ON CONVERSION ERROR, '9999', 'NLS_NUMERIC_CHARACTERS=''.,''') AS marriage_date_y,
         TO_NUMBER((CASE WHEN REGEXP_LIKE(marriage_date_text, '^[0123456789]{4}-[0123456789]{2}-[0123456789]{2}$', 'c') THEN SUBSTR(marriage_date_text, 6, 2) WHEN (REGEXP_LIKE(marriage_date_text, '^[0123456789]{1,2}-[0123456789]{1,2}-[0123456789]{4}$', 'c') OR REGEXP_LIKE(marriage_date_text, '^[0123456789]{1,2}/[0123456789]{1,2}/[0123456789]{4}$', 'c')) THEN REGEXP_SUBSTR(marriage_date_text, '[^-/ ]+', 1, 2, 'c') WHEN REGEXP_LIKE(marriage_date_text, '^[0123456789]{1,2} (JANUARI|FEBRUARI|MARET|APRIL|MEI|JUNI|JULI|AGUSTUS|SEPTEMBER|OKTOBER|NOVEMBER|DESEMBER) [0123456789]{4}$', 'c') THEN CASE REGEXP_SUBSTR(marriage_date_text, '[^-/ ]+', 1, 2, 'c') WHEN 'JANUARI' THEN '1' WHEN 'FEBRUARI' THEN '2' WHEN 'MARET' THEN '3' WHEN 'APRIL' THEN '4' WHEN 'MEI' THEN '5' WHEN 'JUNI' THEN '6' WHEN 'JULI' THEN '7' WHEN 'AGUSTUS' THEN '8' WHEN 'SEPTEMBER' THEN '9' WHEN 'OKTOBER' THEN '10' WHEN 'NOVEMBER' THEN '11' WHEN 'DESEMBER' THEN '12' END END) DEFAULT NULL ON CONVERSION ERROR, '9999', 'NLS_NUMERIC_CHARACTERS=''.,''') AS marriage_date_m,
         TO_NUMBER((CASE WHEN REGEXP_LIKE(marriage_date_text, '^[0123456789]{4}-[0123456789]{2}-[0123456789]{2}$', 'c') THEN SUBSTR(marriage_date_text, 9, 2) WHEN (REGEXP_LIKE(marriage_date_text, '^[0123456789]{1,2}-[0123456789]{1,2}-[0123456789]{4}$', 'c') OR REGEXP_LIKE(marriage_date_text, '^[0123456789]{1,2}/[0123456789]{1,2}/[0123456789]{4}$', 'c')) OR REGEXP_LIKE(marriage_date_text, '^[0123456789]{1,2} (JANUARI|FEBRUARI|MARET|APRIL|MEI|JUNI|JULI|AGUSTUS|SEPTEMBER|OKTOBER|NOVEMBER|DESEMBER) [0123456789]{4}$', 'c') THEN REGEXP_SUBSTR(marriage_date_text, '[^-/ ]+', 1, 1, 'c') END) DEFAULT NULL ON CONVERSION ERROR, '9999', 'NLS_NUMERIC_CHARACTERS=''.,''') AS marriage_date_d
  FROM bounded
),
checked AS (
  SELECT parts.*,
         CASE WHEN birth_date_y BETWEEN 1 AND 9999 AND birth_date_m BETWEEN 1 AND 12 AND birth_date_d BETWEEN 1 AND (CASE WHEN birth_date_m = 2 THEN CASE WHEN (MOD(birth_date_y, 400) = 0 OR (MOD(birth_date_y, 4) = 0 AND MOD(birth_date_y, 100) <> 0)) THEN 29 ELSE 28 END WHEN birth_date_m IN (4,6,9,11) THEN 30 ELSE 31 END) THEN 1 ELSE 0 END AS birth_date_valid,
         CASE WHEN marriage_date_y BETWEEN 1 AND 9999 AND marriage_date_m BETWEEN 1 AND 12 AND marriage_date_d BETWEEN 1 AND (CASE WHEN marriage_date_m = 2 THEN CASE WHEN (MOD(marriage_date_y, 400) = 0 OR (MOD(marriage_date_y, 4) = 0 AND MOD(marriage_date_y, 100) <> 0)) THEN 29 ELSE 28 END WHEN marriage_date_m IN (4,6,9,11) THEN 30 ELSE 31 END) THEN 1 ELSE 0 END AS marriage_date_valid
  FROM parts
)
SELECT job_id,
       case_id,
       document_id,
       cloud_scope_id,
       request_fingerprint,
       status,
       processing_mode,
       job_version,
       current_extraction_version,
       extraction_id,
       extraction_job_version,
       extraction_source_kind,
       schema_version,
       layout_family,
       snapshot_stored_at,
       CAST('1.0.0-db-preview' AS VARCHAR2(30)) AS format_rule_version,
       row_number,
       full_name,
       REGEXP_REPLACE(REGEXP_REPLACE(UPPER(TRIM(REGEXP_REPLACE(full_name, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))), '(^|[^[:alnum:]_])([[:alpha:]])\.([[:alnum:]_])', '\1\2. \3', 1, 0, 'c'), '(^|[^[:alnum:]_])([[:alpha:]])\.([[:alnum:]_])', '\1\2. \3', 1, 0, 'c') AS full_name_canonical,
       REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE(UPPER(TRIM(REGEXP_REPLACE(full_name, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))), '(^|[^[:alnum:]_])([[:alpha:]])\.([[:alnum:]_])', '\1\2. \3', 1, 0, 'c'), '(^|[^[:alnum:]_])([[:alpha:]])\.([[:alnum:]_])', '\1\2. \3', 1, 0, 'c'), ' ', NULL, 1, 0, 'c') AS full_name_search_key,
       nik_raw,
       CAST(CASE WHEN DBMS_LOB.GETLENGTH(REGEXP_REPLACE(nik_raw, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), NULL, 1, 0, 'c')) = 16 AND TRANSLATE(DBMS_LOB.SUBSTR(REGEXP_REPLACE(nik_raw, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), NULL, 1, 0, 'c'), 16, 1), 'x0123456789', 'x') IS NULL THEN DBMS_LOB.SUBSTR(REGEXP_REPLACE(nik_raw, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), NULL, 1, 0, 'c'), 16, 1) END AS VARCHAR2(16 CHAR)) AS nik_canonical,
       CASE WHEN nik_raw IS NULL THEN 'NULL_INPUT' WHEN NVL(DBMS_LOB.GETLENGTH(REGEXP_REPLACE(nik_raw, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), NULL, 1, 0, 'c')),0) = 0 THEN 'BLANK_INPUT' WHEN DBMS_LOB.GETLENGTH(REGEXP_REPLACE(nik_raw, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), NULL, 1, 0, 'c')) = 16 AND TRANSLATE(DBMS_LOB.SUBSTR(REGEXP_REPLACE(nik_raw, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), NULL, 1, 0, 'c'), 16, 1), 'x0123456789', 'x') IS NULL THEN 'FORMATTED' ELSE 'IDENTIFIER_FORMAT' END AS nik_format_status,
       sex,
       UPPER(TRIM(REGEXP_REPLACE(sex, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))) AS sex_canonical,
       birth_place,
       UPPER(TRIM(REGEXP_REPLACE(birth_place, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))) AS birth_place_canonical,
       birth_date_raw,
       CASE WHEN birth_date_valid = 1 THEN TO_DATE((TO_CHAR(birth_date_y, 'FM0000', 'NLS_NUMERIC_CHARACTERS=''.,''') || '-' || TO_CHAR(birth_date_m, 'FM00', 'NLS_NUMERIC_CHARACTERS=''.,''') || '-' || TO_CHAR(birth_date_d, 'FM00', 'NLS_NUMERIC_CHARACTERS=''.,''')), 'FXYYYY-MM-DD', 'NLS_DATE_LANGUAGE=American NLS_CALENDAR=Gregorian') END AS birth_date,
       CASE WHEN birth_date_raw IS NULL THEN 'NULL_INPUT' WHEN NVL(DBMS_LOB.GETLENGTH(UPPER(TRIM(REGEXP_REPLACE(birth_date_raw, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c')))),0) = 0 THEN 'BLANK_INPUT' WHEN birth_date_y IS NULL THEN 'DATE_FORMAT_UNRECOGNIZED' WHEN birth_date_valid = 0 THEN 'INVALID_CALENDAR_DATE' ELSE 'FORMATTED' END AS birth_date_format_status,
       religion,
       UPPER(TRIM(REGEXP_REPLACE(religion, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))) AS religion_canonical,
       education,
       UPPER(TRIM(REGEXP_REPLACE(education, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))) AS education_canonical,
       occupation,
       UPPER(TRIM(REGEXP_REPLACE(occupation, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))) AS occupation_canonical,
       blood_group,
       UPPER(TRIM(REGEXP_REPLACE(blood_group, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))) AS blood_group_canonical,
       marital_status,
       UPPER(TRIM(REGEXP_REPLACE(marital_status, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))) AS marital_status_canonical,
       marriage_date_raw,
       CASE WHEN marriage_date_valid = 1 THEN TO_DATE((TO_CHAR(marriage_date_y, 'FM0000', 'NLS_NUMERIC_CHARACTERS=''.,''') || '-' || TO_CHAR(marriage_date_m, 'FM00', 'NLS_NUMERIC_CHARACTERS=''.,''') || '-' || TO_CHAR(marriage_date_d, 'FM00', 'NLS_NUMERIC_CHARACTERS=''.,''')), 'FXYYYY-MM-DD', 'NLS_DATE_LANGUAGE=American NLS_CALENDAR=Gregorian') END AS marriage_date,
       CASE WHEN marriage_date_raw IS NULL THEN 'NULL_INPUT' WHEN NVL(DBMS_LOB.GETLENGTH(UPPER(TRIM(REGEXP_REPLACE(marriage_date_raw, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c')))),0) = 0 THEN 'BLANK_INPUT' WHEN marriage_date_y IS NULL THEN 'DATE_FORMAT_UNRECOGNIZED' WHEN marriage_date_valid = 0 THEN 'INVALID_CALENDAR_DATE' ELSE 'FORMATTED' END AS marriage_date_format_status,
       relationship_to_head,
       UPPER(TRIM(REGEXP_REPLACE(relationship_to_head, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))) AS relationship_to_head_canonical,
       nationality,
       UPPER(TRIM(REGEXP_REPLACE(nationality, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))) AS nationality_canonical,
       passport_number,
       UPPER(TRIM(REGEXP_REPLACE(passport_number, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))) AS passport_number_canonical,
       residence_permit_number,
       UPPER(TRIM(REGEXP_REPLACE(residence_permit_number, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))) AS residence_permit_number_canonical,
       father_name,
       REGEXP_REPLACE(REGEXP_REPLACE(UPPER(TRIM(REGEXP_REPLACE(father_name, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))), '(^|[^[:alnum:]_])([[:alpha:]])\.([[:alnum:]_])', '\1\2. \3', 1, 0, 'c'), '(^|[^[:alnum:]_])([[:alpha:]])\.([[:alnum:]_])', '\1\2. \3', 1, 0, 'c') AS father_name_canonical,
       REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE(UPPER(TRIM(REGEXP_REPLACE(father_name, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))), '(^|[^[:alnum:]_])([[:alpha:]])\.([[:alnum:]_])', '\1\2. \3', 1, 0, 'c'), '(^|[^[:alnum:]_])([[:alpha:]])\.([[:alnum:]_])', '\1\2. \3', 1, 0, 'c'), ' ', NULL, 1, 0, 'c') AS father_name_search_key,
       mother_name,
       REGEXP_REPLACE(REGEXP_REPLACE(UPPER(TRIM(REGEXP_REPLACE(mother_name, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))), '(^|[^[:alnum:]_])([[:alpha:]])\.([[:alnum:]_])', '\1\2. \3', 1, 0, 'c'), '(^|[^[:alnum:]_])([[:alpha:]])\.([[:alnum:]_])', '\1\2. \3', 1, 0, 'c') AS mother_name_canonical,
       REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE(UPPER(TRIM(REGEXP_REPLACE(mother_name, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))), '(^|[^[:alnum:]_])([[:alpha:]])\.([[:alnum:]_])', '\1\2. \3', 1, 0, 'c'), '(^|[^[:alnum:]_])([[:alpha:]])\.([[:alnum:]_])', '\1\2. \3', 1, 0, 'c'), ' ', NULL, 1, 0, 'c') AS mother_name_search_key,
       combined_parent_name,
       REGEXP_REPLACE(REGEXP_REPLACE(UPPER(TRIM(REGEXP_REPLACE(combined_parent_name, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))), '(^|[^[:alnum:]_])([[:alpha:]])\.([[:alnum:]_])', '\1\2. \3', 1, 0, 'c'), '(^|[^[:alnum:]_])([[:alpha:]])\.([[:alnum:]_])', '\1\2. \3', 1, 0, 'c') AS combined_parent_name_canonical,
       REGEXP_REPLACE(REGEXP_REPLACE(REGEXP_REPLACE(UPPER(TRIM(REGEXP_REPLACE(combined_parent_name, UNISTR('[\0009-\000D\001C-\0020\0085\00A0\1680\2000-\200A\2028\2029\202F\205F\3000]+'), ' ', 1, 0, 'c'))), '(^|[^[:alnum:]_])([[:alpha:]])\.([[:alnum:]_])', '\1\2. \3', 1, 0, 'c'), '(^|[^[:alnum:]_])([[:alpha:]])\.([[:alnum:]_])', '\1\2. \3', 1, 0, 'c'), ' ', NULL, 1, 0, 'c') AS combined_parent_name_search_key
FROM checked~';
  SELECT COUNT(*) INTO v_count FROM KK_OWNER.kk_schema_migrations WHERE migration_version='3-fmt-1.0.0';
  assert_or_create('KK_FMT1_CURRENT_MEMBERS', v_expected, v_count=1);
  SELECT COUNT(*) INTO v_count FROM dba_tab_privs WHERE owner='KK_OWNER' AND table_name='KK_FMT1_CURRENT_HOUSEHOLDS' AND grantee='KK_RUNTIME_ROLE' AND privilege='SELECT';
  IF v_count=0 THEN EXECUTE IMMEDIATE 'GRANT SELECT ON KK_OWNER.KK_FMT1_CURRENT_HOUSEHOLDS TO KK_RUNTIME_ROLE'; END IF;
  SELECT COUNT(*) INTO v_count FROM dba_tab_privs WHERE owner='KK_OWNER' AND table_name='KK_FMT1_CURRENT_MEMBERS' AND grantee='KK_RUNTIME_ROLE' AND privilege='SELECT';
  IF v_count=0 THEN EXECUTE IMMEDIATE 'GRANT SELECT ON KK_OWNER.KK_FMT1_CURRENT_MEMBERS TO KK_RUNTIME_ROLE'; END IF;
  SELECT COUNT(*) INTO v_count FROM KK_OWNER.kk_schema_migrations WHERE migration_version='3-fmt-1.0.0';
  IF v_count=0 THEN
    INSERT INTO KK_OWNER.kk_schema_migrations(migration_version,description)
    VALUES('3-fmt-1.0.0','Additive read-only current snapshot format projection; raw preserved; SQL/Python Unicode parity pending');
    COMMIT;
  END IF;
END;
/
