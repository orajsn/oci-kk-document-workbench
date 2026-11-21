"""Offline scope validation. No SDK, credential reads, DNS or service calls."""
from __future__ import annotations
import copy
import hashlib
import json
import math
from pathlib import Path
import re
from urllib.parse import urlsplit

from .contracts import CloudError

ACK = "APPROVED_KK_CLOUD_OPERATOR_PILOT"
REGION = "ap-batam-1"
DEFAULTS = {"max_attempts": 3, "max_upload_bytes": 10 * 1024 * 1024,
            "lease_seconds": 300, "visibility_seconds": 360,
            "backoff_seconds": 30, "network_connect_timeout_seconds": 5,
            "network_read_timeout_seconds": 30, "queue_poll_seconds": 10}


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CloudError("CONFIG_DUPLICATE_KEY")
        result[key] = value
    return result


def load_config(path: Path) -> dict:
    try:
        with Path(path).open("rb") as handle:
            raw = handle.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise CloudError("CONFIG_TOO_LARGE")
        value = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(CloudError("CONFIG_INVALID_JSON")))
    except CloudError:
        raise
    except Exception:
        raise CloudError("CONFIG_READ_FAILED") from None
    if not isinstance(value, dict):
        raise CloudError("CONFIG_NOT_OBJECT")
    return {**DEFAULTS, **value}


def valid_messages_endpoint(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        url = urlsplit(value)
        hostname = url.hostname or ""
        suffix = ".messaging.ap-batam-1.oci.oraclecloud.com"
        return (url.scheme == "https" and url.port in (None, 443) and not url.username
                and not url.password and not url.query and not url.fragment and url.path in ("", "/")
                and (hostname == suffix[1:] or hostname.endswith(suffix)))
    except ValueError:
        return False


def _string(value, limit=512):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit and "\x00" not in value


def valid_prior_review_scopes(value, current_scope):
    return (isinstance(value, list) and len(value) <= 8
            and all(isinstance(scope, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{2,63}", scope)
                    and scope != current_scope for scope in value)
            and len(set(value)) == len(value))


def issues(config: dict) -> list[str]:
    errors = []
    def require(test, code):
        if not test:
            errors.append(code)
    require(config.get("schema_version") == "1.0", "CONFIG_SCHEMA_REQUIRED")
    require(config.get("enabled") is True and config.get("ack") == ACK, "CLOUD_EXECUTION_NOT_APPROVED")
    require(config.get("data_processing_authorized") is True and config.get("spending_authorized") is True,
            "DATA_AND_SPEND_APPROVAL_REQUIRED")
    require(_string(config.get("authorization_reference")), "AUTHORIZATION_REFERENCE_REQUIRED")
    require(isinstance(config.get("scope_id"), str) and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{2,63}", config["scope_id"])), "SCOPE_ID_REQUIRED")
    if "prior_review_scope_ids" in config:
        require(valid_prior_review_scopes(config["prior_review_scope_ids"], config.get("scope_id")),
                "PRIOR_REVIEW_SCOPE_IDS_INVALID")
    require(config.get("region") == REGION, "BATAM_REGION_REQUIRED")
    if "upload_image_check_enabled" in config:
        require(type(config["upload_image_check_enabled"]) is bool, "UPLOAD_IMAGE_CHECK_FLAG_INVALID")
    require(str(config.get("compartment_id", "")).startswith("ocid1.compartment."), "COMPARTMENT_REQUIRED")
    require(_string(config.get("object_storage_namespace"), 256), "OBJECT_NAMESPACE_REQUIRED")
    require(isinstance(config.get("object_bucket"), str) and bool(re.fullmatch(r"[A-Za-z0-9_-]{1,256}", config["object_bucket"])), "BUCKET_REQUIRED")
    require(str(config.get("queue_id", "")).startswith("ocid1.queue.") and REGION in str(config.get("queue_id", "")), "BATAM_QUEUE_REQUIRED")
    require(valid_messages_endpoint(config.get("queue_messages_endpoint")), "BATAM_QUEUE_MESSAGES_ENDPOINT_REQUIRED")
    ranges = {"max_requests": (1, 10000), "max_attempts": (1, 5), "max_upload_bytes": (1024, 50 * 1024 * 1024),
              "lease_seconds": (180, 3600), "visibility_seconds": (210, 43200), "backoff_seconds": (1, 300),
              "network_connect_timeout_seconds": (1, 10), "network_read_timeout_seconds": (1, 60), "queue_poll_seconds": (0, 20)}
    for name, (low, high) in ranges.items():
        value = config.get(name)
        require(type(value) is int and low <= value <= high, name.upper() + "_INVALID")
    if type(config.get("lease_seconds")) is int and type(config.get("visibility_seconds")) is int:
        require(config["visibility_seconds"] >= config["lease_seconds"] + 30, "VISIBILITY_MUST_EXCEED_LEASE")
    if type(config.get("queue_poll_seconds")) is int and type(config.get("network_read_timeout_seconds")) is int:
        require(config["network_read_timeout_seconds"] > config["queue_poll_seconds"], "POLL_MUST_FIT_READ_TIMEOUT")
    auth = config.get("oci_auth", {})
    if not isinstance(auth, dict):
        auth = {}
    require(auth.get("mode") in ("instance_principal", "config_file"), "EXPLICIT_OCI_AUTH_REQUIRED")
    if auth.get("mode") == "config_file":
        require(_string(auth.get("config_path")), "EXPLICIT_OCI_CONFIG_PATH_REQUIRED")
        require(_string(auth.get("profile")), "EXPLICIT_OCI_PROFILE_REQUIRED")
    database = config.get("database", {})
    if not isinstance(database, dict):
        database = {}
    require(database.get("user") == "KK_RUNTIME", "RUNTIME_DB_USER_REQUIRED")
    require(str(database.get("id", "")).startswith("ocid1.autonomousdatabase.") and REGION in str(database.get("id", "")), "BATAM_DATABASE_REQUIRED")
    host = database.get("host", "")
    require(isinstance(host, str) and bool(re.fullmatch(r"[A-Za-z0-9.-]+", host))
            and host.endswith(".oraclecloud.com") and ".ap-batam-1." in host, "APPROVED_DATABASE_HOST_REQUIRED")
    require(isinstance(database.get("service_name"), str) and bool(re.fullmatch(r"[A-Za-z0-9_.-]{1,256}", database["service_name"])), "DATABASE_SERVICE_REQUIRED")
    require(database.get("private_endpoint_verified") is True, "PRIVATE_DATABASE_VERIFICATION_REQUIRED")
    for key in ("password_env", "wallet_dir_env", "wallet_password_env"):
        require(bool(re.fullmatch(r"KK_CLOUD_[A-Z0-9_]{1,80}", str(database.get(key, "")))), "DATABASE_SECRET_REFERENCE_REQUIRED")
    adapter = config.get("oci_adapter", {})
    if not isinstance(adapter, dict):
        adapter = {}
    three_way_nik = adapter.get("three_way_nik_enabled", False)
    require(type(three_way_nik) is bool, "THREE_WAY_NIK_CONFIG_INVALID")
    three_way_manifest_keys = {"three_way_nik_manifest_path", "three_way_nik_manifest_sha256"}
    if three_way_nik is True:
        require(adapter.get("pipeline") == "gemini_image_only", "THREE_WAY_NIK_PIPELINE_REQUIRED")
        require(adapter.get("focused_refinement_enabled") is True
                and type(adapter.get("local_ocr")) is dict and adapter["local_ocr"].get("enabled") is True
                and adapter.get("nik_row_alignment_enabled") is True
                and adapter.get("field_corroboration_enabled") is True
                and adapter.get("approved_document_service") is True
                and adapter.get("diagnostic_response_dir") == "/var/lib/kk-pilot/diagnostics",
                "THREE_WAY_NIK_PREREQUISITES_REQUIRED")
        require(adapter.get("three_way_nik_manifest_path") == "/opt/kk-pilot/three-way-nik-v1/source-geometry-manifest.json"
                and isinstance(adapter.get("three_way_nik_manifest_sha256"), str)
                and bool(re.fullmatch(r"[0-9a-f]{64}", adapter["three_way_nik_manifest_sha256"])),
                "THREE_WAY_NIK_MANIFEST_INVALID")
        require(type(adapter.get("maximum_provider_calls_per_job")) is int
                and adapter.get("maximum_provider_calls_per_job") == 5,
                "THREE_WAY_NIK_CALL_BUDGET_INVALID")
    else:
        require(not three_way_manifest_keys.intersection(adapter), "THREE_WAY_NIK_PARTIAL_CONFIG")
    try:
        if adapter.get("pipeline", "genai") == "gemini_image_only":
            from ..adapters.oci_gemini import check_approval
        elif adapter.get("pipeline", "genai") == "document_meta_multiview":
            from ..adapters.oci_meta_hybrid import check_approval
        elif adapter.get("pipeline", "genai") == "document_genai_hybrid":
            from ..adapters.oci_hybrid import check_approval
        elif adapter.get("pipeline", "genai") == "genai":
            from ..adapters.oci_genai import check_approval
        else:
            raise CloudError("PIPELINE_NOT_SUPPORTED")
        check_approval(adapter)
    except Exception as exc:
        errors.append(getattr(exc, "code", "INFERENCE_APPROVAL_INVALID"))
    if adapter.get("pipeline") == "document_meta_multiview":
        require(config.get("max_attempts") == 1, "META_RETRY_NOT_ALLOWED")
    if adapter.get("pipeline") == "gemini_image_only":
        require(config.get("max_attempts") == 1, "GEMINI_RETRY_NOT_ALLOWED")
        local_ocr = adapter.get("local_ocr", {})
        require(type(local_ocr) is dict and type(local_ocr.get("enabled", False)) is bool,
                "LOCAL_OCR_CONFIG_INVALID")
        field_corroboration = adapter.get("field_corroboration_enabled", False)
        require(type(field_corroboration) is bool, "FIELD_CORROBORATION_CONFIG_INVALID")
        if field_corroboration is True:
            require(type(local_ocr) is dict and local_ocr.get("enabled") is True
                    and adapter.get("focused_refinement_enabled") is True
                    and adapter.get("diagnostic_response_dir") == "/var/lib/kk-pilot/diagnostics",
                    "FIELD_CORROBORATION_PREREQUISITES_REQUIRED")
        row_alignment = adapter.get("nik_row_alignment_enabled", False)
        require(type(row_alignment) is bool, "ROW_ALIGNMENT_CONFIG_INVALID")
        if row_alignment is True:
            require(type(local_ocr) is dict and local_ocr.get("enabled") is True
                    and adapter.get("focused_refinement_enabled") is True
                    and adapter.get("diagnostic_response_dir") == "/var/lib/kk-pilot/diagnostics"
                    and type(adapter.get("maximum_provider_calls_per_job")) is int
                    and adapter.get("maximum_provider_calls_per_job") == (5 if three_way_nik is True else 3),
                    "ROW_ALIGNMENT_PREREQUISITES_REQUIRED")
        if type(local_ocr) is dict and local_ocr.get("enabled") is True:
            require(adapter.get("focused_refinement_enabled") is True, "LOCAL_OCR_REQUIRES_FOCUS")
            require(type(adapter.get("maximum_provider_calls_per_job")) is int
                    and adapter.get("maximum_provider_calls_per_job") == (5 if three_way_nik is True else 3), "LOCAL_OCR_CALL_BUDGET_INVALID")
            require(set(local_ocr) == {"enabled", "model_manifest_path", "model_manifest_sha256", "timeout_seconds"}
                    and local_ocr.get("model_manifest_path") == "/opt/kk-pilot/ocr-runtime-v1/model-manifest.json"
                    and isinstance(local_ocr.get("model_manifest_sha256"), str)
                    and bool(re.fullmatch(r"[0-9a-f]{64}", local_ocr["model_manifest_sha256"]))
                    and type(local_ocr.get("timeout_seconds")) is int
                    and 1 <= local_ocr["timeout_seconds"] <= 60, "LOCAL_OCR_CONFIG_INVALID")
    require(adapter.get("region") == config.get("region"), "INFERENCE_ENTRY_REGION_MISMATCH")
    if adapter.get("pipeline") in ("document_genai_hybrid", "document_meta_multiview", "gemini_image_only"):
        require(adapter.get("compartment_id") == config.get("compartment_id"), "INFERENCE_COMPARTMENT_MISMATCH")
    require(adapter.get("authorization_reference") == config.get("authorization_reference"), "INFERENCE_AUTHORIZATION_MISMATCH")
    require(adapter.get("max_requests") == config.get("max_requests"), "INFERENCE_BUDGET_MISMATCH")
    require(adapter.get("auth") == auth.get("mode"), "INFERENCE_AUTH_IDENTITY_MISMATCH")
    if auth.get("mode") == "config_file":
        require(adapter.get("oci_config_path") == auth.get("config_path") and adapter.get("oci_profile") == auth.get("profile"), "INFERENCE_AUTH_IDENTITY_MISMATCH")
    for name, default in (("connect_timeout_seconds", 5), ("read_timeout_seconds", 30)):
        value = adapter.get(name, default)
        require(type(value) in (int, float) and math.isfinite(value) and 0 < value <= 120,
                "INFERENCE_TIMEOUT_INVALID")
    # Complete object read + profile lookup + inference should normally fit the lease.
    # SDK read timeouts are not absolute wall-clock deadlines; fencing remains essential.
    try:
        if adapter.get("pipeline") in ("document_genai_hybrid", "document_meta_multiview"):
            call_bound = (5 + 30) + (5 + 90) + (5 + 120)
        else:
            local = adapter.get("local_ocr")
            ocr_enabled = adapter.get("pipeline") == "gemini_image_only" and isinstance(local, dict) and local.get("enabled") is True
            calls = 4 if ocr_enabled else 3 if adapter.get("pipeline") == "gemini_image_only" and adapter.get("focused_refinement_enabled") is True else 2
            if adapter.get("pipeline") == "gemini_image_only" and three_way_nik is True:
                calls += 2  # One Batam Document Understanding call and one NIK-only Gemini call.
            call_bound = calls * (float(adapter.get("connect_timeout_seconds", 5)) + float(adapter.get("read_timeout_seconds", 30)))
            if ocr_enabled:
                timeout = local.get("timeout_seconds")
                call_bound += timeout if type(timeout) is int and 1 <= timeout <= 60 else 60
        call_bound += config["network_connect_timeout_seconds"] + config["network_read_timeout_seconds"] + 60
        require(config["lease_seconds"] > call_bound, "LEASE_TOO_SHORT_FOR_CONFIGURED_TIMEOUTS")
    except (TypeError, ValueError, KeyError):
        errors.append("LEASE_TIMEOUT_CONFIGURATION_INVALID")
    forbidden = {"password", "private_key", "key_content", "wallet_password", "passphrase", "token"}
    def inspect(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key.lower() in forbidden:
                    errors.append("INLINE_SECRET_NOT_ALLOWED")
                inspect(item)
        elif isinstance(value, list):
            for item in value:
                inspect(item)
    inspect(config)
    return sorted(set(errors))


def validate_config(config: dict) -> dict:
    result = copy.deepcopy({**DEFAULTS, **config})
    if issues(result):
        raise CloudError("CLOUD_CONFIG_NOT_READY")
    identity = copy.deepcopy(result)
    for local_key in ("config_sha256", "private_temp_dir"):
        identity.pop(local_key, None)
    # A path may differ between the two approved VMs; it is not the data scope.
    identity.get("oci_auth", {}).pop("config_path", None)
    identity.get("oci_adapter", {}).pop("oci_config_path", None)
    result["config_sha256"] = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    return result


def offline_report(config: dict, command: str) -> dict:
    errors = issues({**DEFAULTS, **config})
    adapter = config.get("oci_adapter")
    adapter = adapter if isinstance(adapter, dict) else {}
    local = adapter.get("local_ocr")
    three_calls = adapter.get("pipeline") == "gemini_image_only" and isinstance(local, dict) and local.get("enabled") is True
    two_calls = (adapter.get("pipeline") in ("document_genai_hybrid", "document_meta_multiview")
                 or (adapter.get("pipeline") == "gemini_image_only" and adapter.get("focused_refinement_enabled") is True))
    max_attempts = config.get("max_requests") if type(config.get("max_requests")) is int and 1 <= config["max_requests"] <= 10000 else None
    three_way_nik = adapter.get("pipeline") == "gemini_image_only" and adapter.get("three_way_nik_enabled") is True
    calls_per_attempt = 5 if three_way_nik else 3 if three_calls else 2 if two_calls else 1
    document_calls = int(three_way_nik or adapter.get("pipeline") in ("document_genai_hybrid", "document_meta_multiview"))
    genai_calls = calls_per_attempt - document_calls
    return {"action": command, "mode": "offline_only", "cloud_calls": 0, "writes": 0,
            "configuration_ready": not errors, "configuration_issues": errors,
            "region": REGION if config.get("region") == REGION else None,
            "maximum_scope_attempts": max_attempts,
            "maximum_provider_calls_per_attempt": calls_per_attempt,
            "maximum_genai_calls_per_attempt": genai_calls,
            "maximum_document_understanding_calls_per_attempt": document_calls,
            "maximum_possible_provider_calls": max_attempts * calls_per_attempt if max_attempts is not None else None,
            "budget_note": "Scope attempts are logical job attempts, not a provider-call budget. Provider counts are configured per-attempt bounds; routing profile lookup is separate. No live reservation or database budget enforcement is verified by this offline report.",
            "workflow": ["immutable_object", "oracle_job_and_outbox_commit", "queue_publish",
                         "fenced_oracle_claim", "approved_inference", "oracle_candidate_commit", "queue_ack"],
            "limitation": "operator pilot; hosted UI/auth and live Oracle/OCI checks remain separate"}
