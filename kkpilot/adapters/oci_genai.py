"""Optional live OCI adapter. No network or credential access until explicitly enabled.

Checked against Oracle Python SDK 2.190/2.191 documentation, 12 Nov 2026.
SDK request construction and response handling have isolated fake-SDK tests.
"""
import base64
import copy
import hashlib
import json
import os
import re
import stat
import threading
import time
import uuid
from pathlib import Path
from collections.abc import Mapping
from .errors import AdapterError
from .preprocess import prepare_image
from .compact_contract import (
    PROMPT_VERSION as COMPACT_PROMPT_VERSION, WIRE_VERSION as COMPACT_WIRE_VERSION,
    build_prompt as build_compact_prompt, build_cohere_prompt, build_wire_schema, build_cohere_wire_schema, expand_response,
)
from .named_contract import (
    WIRE_VERSION as NAMED_WIRE_VERSION, PROMPT_VERSION as NAMED_PROMPT_VERSION,
    build_named_prompt, build_named_wire_schema, build_named_cohere_schema, expand_named_response,
)
from ..validation import PayloadError

ROOT = Path(__file__).resolve().parents[2]
DIAGNOSTIC_RESPONSE_DIR = "/var/lib/kk-pilot/diagnostics"
DIAGNOSTIC_MAX_BYTES = 2 * 1024 * 1024

def _diagnostic_enabled(config):
    if "diagnostic_response_dir" not in config:
        return False
    if config["diagnostic_response_dir"] != DIAGNOSTIC_RESPONSE_DIR:
        raise AdapterError("DIAGNOSTIC_DIRECTORY_NOT_ALLOWED")
    return True

def _diagnostic_directory_fd():
    """Open each fixed path component without following symlinks; create nothing."""
    if not all(hasattr(os, name) for name in ("getuid", "O_DIRECTORY", "O_NOFOLLOW")):
        raise AdapterError("DIAGNOSTIC_PLATFORM_UNSUPPORTED")
    current = None
    try:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        current = os.open("/", flags)
        for component in ("var", "lib", "kk-pilot", "diagnostics"):
            child = os.open(component, flags, dir_fd=current)
            os.close(current)
            current = child
        info = os.fstat(current)
        if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700 or info.st_uid != os.getuid():
            raise AdapterError("DIAGNOSTIC_DIRECTORY_UNSAFE")
        result, current = current, None
        return result
    except OSError:
        raise AdapterError("DIAGNOSTIC_DIRECTORY_UNSAFE") from None
    finally:
        if current is not None:
            os.close(current)

def _diagnostic_preflight(config, context):
    if not _diagnostic_enabled(config):
        return None
    identifiers = []
    for key in ("job_id", "attempt_id"):
        value = context.get(key)
        if not isinstance(value, str) or not re.fullmatch(r"(?:[0-9a-fA-F]{32}|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})", value):
            raise AdapterError("DIAGNOSTIC_IDENTIFIER_INVALID")
        identifiers.append(str(uuid.UUID(value)))
    directory = _diagnostic_directory_fd()
    os.close(directory)
    return ".".join(identifiers)

def _write_diagnostic(filename, record):
    """Create one private, bounded JSON record; never overwrite or print content."""
    identifier = r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"
    if not isinstance(filename, str) or not re.fullmatch(identifier + r"\." + identifier + r"\.(?:response|metadata|focused\.response|ocr\.raw|hybrid\.response|hybrid\.evidence|three-way-nik\.(?:du\.response|response|evidence))\.json", filename):
        raise AdapterError("DIAGNOSTIC_IDENTIFIER_INVALID")
    try:
        data = json.dumps(record, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise AdapterError("DIAGNOSTIC_SERIALIZATION_FAILED") from None
    if len(data) > DIAGNOSTIC_MAX_BYTES:
        raise AdapterError("DIAGNOSTIC_RESPONSE_TOO_LARGE")
    directory = _diagnostic_directory_fd()
    descriptor = None
    try:
        descriptor = os.open(filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise AdapterError("DIAGNOSTIC_FILE_UNSAFE")
        pending = memoryview(data)
        while pending:
            written = os.write(descriptor, pending)
            if written <= 0:
                raise OSError("diagnostic write did not progress")
            pending = pending[written:]
        os.fsync(descriptor)
        os.fsync(directory)
    except OSError:
        raise AdapterError("DIAGNOSTIC_WRITE_FAILED") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(directory)

_COMPACT_VALIDATION_REASONS = {
    "Named response must be an object.": "OBJECT_REQUIRED",
    "Named response has missing or unsupported top-level fields.": "TOP_LEVEL_FIELDS",
    "Unsupported named wire version.": "WIRE_VERSION",
    "Each named section must contain only v and e.": "NAMED_SECTION_KEYS",
    "Named household value keys must match canonical fields.": "NAMED_HOUSEHOLD_FIELDS",
    "Named member value keys must match canonical fields.": "NAMED_MEMBER_FIELDS",
    "Named response exceeds the 2 MiB local record limit.": "WIRE_SIZE",
    "Compact response must be an object.": "OBJECT_REQUIRED",
    "Compact response has missing or unsupported top-level fields.": "TOP_LEVEL_FIELDS",
    "Unsupported compact wire version.": "WIRE_VERSION",
    "Invalid layout family.": "LAYOUT_FAMILY",
    "Members must be an array of at most 100 rows.": "MEMBERS_SHAPE",
    "Each compact vector must contain only v and e.": "VECTOR_KEYS",
    "Wrong value vector length.": "VALUE_VECTOR_LENGTH",
    "Invalid or missing evidence states.": "EVIDENCE_VECTOR",
    "Printed member row number must be an integer from 1 to 100.": "ROW_IDENTIFIER",
    "Values must be bounded strings or null.": "VALUE_TYPE_OR_BOUND",
    "Duplicate printed member row number.": "DUPLICATE_ROW_IDENTIFIER",
    "Invalid field reference.": "FIELD_REFERENCE",
    "Field reference must identify a supplied field and printed row.": "FIELD_REFERENCE_TARGET",
    "Notes must be a bounded array.": "NOTES_SHAPE",
    "Each note must be [reference, note].": "NOTE_SHAPE",
    "Duplicate evidence note target.": "DUPLICATE_NOTE",
    "Invalid evidence note.": "NOTE_TEXT",
    "Flags must be a bounded array.": "FLAGS_SHAPE",
    "Each flag must be [reference, code, message].": "FLAG_SHAPE",
    "Invalid review flag text.": "FLAG_TEXT",
    "Compact response exceeds the 2 MiB local record limit.": "WIRE_SIZE",
    "Extraction exceeds the 2 MiB local record limit.": "DOMAIN_SIZE",
}

def _compact_validation_reason(error):
    message = getattr(error, "message", None)
    return _COMPACT_VALIDATION_REASONS.get(message, "DOMAIN_OR_UNKNOWN") if isinstance(message, str) else "DOMAIN_OR_UNKNOWN"

def selected_contract(config):
    contract = config.get("extraction_contract", "full-1.0")
    if contract not in ("full-1.0", COMPACT_WIRE_VERSION, NAMED_WIRE_VERSION):
        raise AdapterError("EXTRACTION_CONTRACT_UNSUPPORTED")
    return contract

def selected_response_format(config):
    contract = selected_contract(config)
    if "response_format" not in config:
        if contract == NAMED_WIRE_VERSION:
            raise AdapterError("NAMED_RESPONSE_FORMAT_REQUIRED")
        return None
    if config["response_format"] != "cohere_json_schema":
        raise AdapterError("RESPONSE_FORMAT_UNSUPPORTED")
    if config.get("model_family") != "cohere_v2" or contract not in (COMPACT_WIRE_VERSION, NAMED_WIRE_VERSION):
        raise AdapterError("RESPONSE_FORMAT_REQUIRES_COHERE_COMPACT")
    return "cohere_json_schema"

def _server_schema(config):
    return build_named_cohere_schema() if selected_contract(config) == NAMED_WIRE_VERSION else build_cohere_wire_schema()

def parse_json_response(text):
    if not isinstance(text, str) or len(text) > 2_000_000:
        raise AdapterError("MODEL_RESPONSE_SIZE")
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines[0].strip().lower() not in ("```", "```json") or lines[-1].strip() != "```":
            raise AdapterError("MODEL_JSON_INVALID")
        text = "\n".join(lines[1:-1])
    def unique_pairs(pairs):
        obj = {}
        for k, v in pairs:
            if k in obj:
                raise AdapterError("MODEL_JSON_DUPLICATE_KEY")
            obj[k] = v
        return obj
    try:
        value = json.loads(text, object_pairs_hook=unique_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))
    except AdapterError:
        raise
    except (ValueError, RecursionError) as exc:
        raise AdapterError("MODEL_JSON_INVALID") from exc
    if not isinstance(value, dict):
        raise AdapterError("MODEL_JSON_NOT_OBJECT")
    return value

def check_approval(config):
    for flag in ("allow_live_calls", "data_processing_authorized", "spending_authorized"):
        if config.get(flag) is not True:
            raise AdapterError("LIVE_APPROVAL_REQUIRED")
    if any(not isinstance(config.get(k), str) or not config[k].strip() or len(config[k]) > 500
           for k in ("authorization_reference", "dataset_id")):
        raise AdapterError("APPROVAL_SCOPE_REQUIRED")
    if config.get("region") != "ap-batam-1":
        raise AdapterError("BATAM_ACCESS_REGION_REQUIRED")
    allowed = config.get("approved_serving_regions")
    if not isinstance(allowed, list) or not allowed or any(not isinstance(x, str) or not x for x in allowed):
        raise AdapterError("APPROVED_REGIONS_REQUIRED")
    if not str(config.get("routing_profile_id", "")).startswith("ocid1."):
        raise AdapterError("ROUTING_PROFILE_REQUIRED")
    if not str(config.get("compartment_id", "")).startswith("ocid1.compartment."):
        raise AdapterError("COMPARTMENT_REQUIRED")
    family = config.get("model_family")
    model = config.get("expected_model_id", "")
    if not isinstance(model, str) or not ((family == "cohere_v2" and model.startswith("cohere.")) or
            (family == "generic" and model.startswith("meta."))):
        raise AdapterError("OCI_HOSTED_VISION_MODEL_REQUIRED")
    if config.get("vision_api_compatibility_verified") is not True:
        raise AdapterError("MODEL_API_VERIFICATION_REQUIRED")
    budget = config.get("max_requests")
    if type(budget) is not int or not 1 <= budget <= 10000:
        raise AdapterError("REQUEST_BUDGET_REQUIRED")
    hashes = config.get("approved_image_sha256s")
    if not isinstance(hashes, list) or not hashes or any(not isinstance(x, str) or not re.fullmatch(r"[0-9a-f]{64}", x) for x in hashes):
        raise AdapterError("APPROVED_IMAGE_HASHES_REQUIRED")
    selected_contract(config)
    selected_response_format(config)
    _diagnostic_enabled(config)

def verify_routing_profile(profile, config):
    if profile.get("lifecycle_state") != "ACTIVE":
        raise AdapterError("ROUTING_PROFILE_NOT_ACTIVE")
    if profile.get("id") != config["routing_profile_id"]:
        raise AdapterError("ROUTING_PROFILE_MISMATCH")
    regions = (profile.get("region_routing_policy") or {}).get("allowed_regions")
    models = (profile.get("model_routing_policy") or {}).get("allowed_models")
    if not isinstance(regions, list) or not regions or not set(regions).issubset(config["approved_serving_regions"]):
        raise AdapterError("ROUTING_POLICY_NOT_APPROVED")
    if models != [config["expected_model_id"]]:
        raise AdapterError("ROUTING_MODEL_NOT_APPROVED")

def build_chat_request(oci, config, image_bytes, prompt):
    response_format = selected_response_format(config)
    m = oci.generative_ai_inference.models
    uri = "data:image/jpeg;base64," + base64.b64encode(image_bytes).decode("ascii")
    maximum = int(config.get("max_output_tokens", 6000))
    if not 128 <= maximum <= 16000:
        raise AdapterError("OUTPUT_LIMIT_INVALID")
    if config["model_family"] == "cohere_v2":
        response_options = {}
        if response_format is not None:
            response_type = getattr(m, "CohereResponseJsonFormat", None)
            if not callable(response_type):
                raise AdapterError("OCI_SDK_RESPONSE_FORMAT_UNSUPPORTED")
            response_options["response_format"] = response_type(schema=_server_schema(config))
        request = m.CohereChatRequestV2(messages=[
            m.CohereSystemMessageV2(content=[m.CohereTextContentV2(text=prompt)]),
            m.CohereUserMessageV2(content=[m.CohereImageContentV2(
                image_url=m.CohereImageUrlV2(url=uri))])],
            max_tokens=maximum, temperature=0, is_stream=False, **response_options)
    else:
        request = m.GenericChatRequest(messages=[
            m.SystemMessage(content=[m.TextContent(text=prompt)]),
            m.UserMessage(content=[m.ImageContent(image_url=m.ImageUrl(url=uri, detail="AUTO"))])],
            max_tokens=maximum, temperature=0, is_stream=False)
    return m.ChatDetails(compartment_id=config["compartment_id"],
        serving_mode=m.OnDemandServingMode(model_id=config["routing_profile_id"]),
        chat_request=request)

_USAGE_COUNTERS = (
    "input_tokens", "output_tokens", "total_tokens", "prompt_tokens", "completion_tokens",
    "cached_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "reasoning_tokens",
    "audio_tokens", "image_tokens", "accepted_prediction_tokens", "rejected_prediction_tokens",
)
_USAGE_GROUPS = ("billed_units", "tokens", "prompt_tokens_details", "completion_tokens_details")
_FINISH_REASONS = frozenset((
    "COMPLETE", "STOP", "STOP_SEQUENCE", "END_TURN", "MAX_TOKENS", "LENGTH", "ERROR",
    "ERROR_LIMIT", "ERROR_TOXIC", "USER_CANCEL", "USER_CANCELLED", "CONTENT_FILTER",
    "TOOL_CALLS", "FUNCTION_CALL", "ERROR_TOXIC_GENERATION", "ERROR_TOXIC_INPUT",
))

def _safe_usage(usage, depth=0):
    if not isinstance(usage, dict) or depth > 2:
        return None
    result = {key: usage[key] for key in _USAGE_COUNTERS
              if type(usage.get(key)) is int and 0 <= usage[key] <= 1_000_000_000}
    for key in _USAGE_GROUPS:
        nested = _safe_usage(usage.get(key), depth + 1)
        if nested is not None:
            result[key] = nested
    return result

def response_metadata(response, family):
    """Collect only allowlisted diagnostics, including from unparseable output."""
    if not isinstance(response, dict):
        return {}
    chat = response.get("chat_response")
    chat = chat if isinstance(chat, dict) else {}
    finish = chat.get("finish_reason")
    if family != "cohere_v2":
        choices = chat.get("choices")
        finish = choices[0].get("finish_reason") if isinstance(choices, list) and len(choices) == 1 and isinstance(choices[0], dict) else None
    result = {}
    if isinstance(finish, str):
        result["finish_reason"] = finish if finish.upper() in _FINISH_REASONS else "UNKNOWN"
    usage = _safe_usage(chat.get("usage") or response.get("usage"))
    if usage is not None:
        result["usage"] = usage
    return result

def _header_metadata(headers):
    if not isinstance(headers, Mapping):
        return {}
    result = {}
    served = headers.get("x-genai-selected-region")
    if served is not None:
        # Keep bounded region identifiers for the existing exact approved-region
        # check. Never turn an unexpected identifier into absent metadata.
        if isinstance(served, str) and re.fullmatch(r"[a-z][a-z0-9-]{0,79}", served):
            result["served_region"] = served
        else:
            result["served_region_invalid"] = True
    request_id = headers.get("opc-request-id")
    if isinstance(request_id, str) and re.fullmatch(r"(?:[A-Za-z0-9_-]{1,200}|[A-Fa-f0-9]{16,128}(?:/[A-Fa-f0-9]{16,128}){1,4})", request_id):
        result["request_id"] = request_id
    return result

def extract_response(response, family):
    if not isinstance(response, dict) or not isinstance(response.get("chat_response"), dict):
        raise AdapterError("MODEL_RESPONSE_SHAPE")
    chat = response["chat_response"]
    if family == "cohere_v2":
        finish = chat.get("finish_reason", "")
        message = chat.get("message")
    else:
        choices = chat.get("choices", [])
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
            raise AdapterError("MODEL_RESPONSE_SHAPE")
        finish = choices[0].get("finish_reason", "")
        message = choices[0].get("message")
    if not isinstance(finish, str) or finish.upper() not in ("COMPLETE", "STOP", "STOP_SEQUENCE", "END_TURN"):
        raise AdapterError("MODEL_RESPONSE_INCOMPLETE")
    if not isinstance(message, dict):
        raise AdapterError("MODEL_RESPONSE_SHAPE")
    contents = message.get("content", [])
    if not isinstance(contents, list):
        raise AdapterError("MODEL_RESPONSE_SHAPE")
    parts = [x.get("text", "") for x in contents if isinstance(x, dict) and x.get("type") == "TEXT"]
    if not all(isinstance(part, str) for part in parts):
        raise AdapterError("MODEL_RESPONSE_SHAPE")
    return parse_json_response("".join(parts)), response_metadata(response, family)

class OciGenAIExtractor:
    def __init__(self, config):
        check_approval(config)  # Must precede imports, auth lookup or network access.
        self.config = copy.deepcopy(config)
        self.requests = 0
        self._lock = threading.Lock()
        self._local = threading.local()

    @property
    def last_metadata(self):
        return getattr(self._local, "metadata", {})

    def extract(self, image_path: Path, context: dict):
        self._local.metadata = {}
        contract = selected_contract(self.config)  # Also rejects post-construction changes before SDK access.
        response_format = selected_response_format(self.config)
        diagnostic_stem = _diagnostic_preflight(self.config, context)
        diagnostic_captured = False
        limit = int(self.config.get("max_upload_bytes", 10 * 1024 * 1024))
        if not 1024 <= limit <= 50 * 1024 * 1024:
            raise AdapterError("IMAGE_TOO_LARGE")
        try:
            with Path(image_path).open("rb") as source:
                source_bytes = source.read(limit + 1)
        except OSError:
            raise AdapterError("SOURCE_READ_FAILED") from None
        if len(source_bytes) > limit:
            raise AdapterError("IMAGE_TOO_LARGE")
        digest = hashlib.sha256(source_bytes).hexdigest()
        if digest not in self.config["approved_image_sha256s"] or context.get("sha256") != digest:
            raise AdapterError("SOURCE_NOT_APPROVED")
        with self._lock:
            if self.requests >= self.config["max_requests"]:
                raise AdapterError("REQUEST_BUDGET_EXHAUSTED")
        try:
            import oci
        except ImportError as exc:
            raise AdapterError("OCI_SDK_NOT_INSTALLED") from exc
        cfg = self.config
        if not hasattr(oci.generative_ai.GenerativeAiClient, "get_routing_profile"):
            raise AdapterError("OCI_SDK_CONTRACT_UNSUPPORTED")
        if cfg.get("auth") == "instance_principal":
            oci_config = {"region": cfg["region"]}
            auth = {"signer": oci.auth.signers.InstancePrincipalsSecurityTokenSigner()}
        else:
            if not cfg.get("oci_config_path"):
                raise AdapterError("EXPLICIT_OCI_CONFIG_REQUIRED")
            oci_config = oci.config.from_file(cfg["oci_config_path"], cfg.get("oci_profile", "DEFAULT"))
            oci_config["region"] = cfg["region"]
            auth = {}
        timeout = (float(cfg.get("connect_timeout_seconds", 5)), float(cfg.get("read_timeout_seconds", 30)))
        if any(x <= 0 or x > 120 for x in timeout):
            raise AdapterError("TIMEOUT_INVALID")
        start = time.perf_counter()
        try:
            management = oci.generative_ai.GenerativeAiClient(oci_config, timeout=timeout, **auth)
            profile = management.get_routing_profile(cfg["routing_profile_id"],
                retry_strategy=oci.retry.NoneRetryStrategy())
            verify_routing_profile(oci.util.to_dict(profile.data), cfg)
            # Only these immutable, hash-approved bytes may be sent. Never reopen the path.
            prepared_policy = (cfg.get("preprocessing_by_sha256") or {}).get(digest, {})
            image, image_meta = prepare_image(source_bytes, max_bytes=limit, max_edge=int(cfg.get("max_image_edge", 2400)),
                crop=context.get("crop") if context.get("crop") is not None else prepared_policy.get("crop"),
                rotate=context.get("rotate") if context.get("rotate") is not None else prepared_policy.get("rotate", 0))
            schema = json.loads((ROOT / "schemas/kk-extraction.schema.json").read_text(encoding="utf-8"))
            if contract == NAMED_WIRE_VERSION:
                prompt = build_named_prompt()
                wire_schema = build_named_wire_schema()
                prompt_version = NAMED_PROMPT_VERSION
            elif contract == COMPACT_WIRE_VERSION:
                prompt = build_cohere_prompt() if response_format is not None else build_compact_prompt()
                wire_schema = build_wire_schema()
                prompt_version = COMPACT_PROMPT_VERSION
            else:
                prompt = (ROOT / "schemas/extraction-prompt.txt").read_text(encoding="utf-8")
                prompt += "\nJSON SCHEMA:\n" + json.dumps(schema, separators=(",", ":"))
                wire_schema = schema
                prompt_version = "1.0"
            details = build_chat_request(oci, cfg, image, prompt)
            client = oci.generative_ai_inference.GenerativeAiInferenceClient(oci_config,
                timeout=timeout, **auth)
            token = str(uuid.uuid5(uuid.NAMESPACE_URL,
                str(context.get("job_id")) + ":" + str(context.get("attempt_id"))))
            with self._lock:
                if self.requests >= cfg["max_requests"]:
                    raise AdapterError("REQUEST_BUDGET_EXHAUSTED")
                self.requests += 1
            self._local.metadata = {"mode": "oci", "elapsed_seconds": time.perf_counter() - start,
                "preprocessing": image_meta, "response": {},
                "entry_region": cfg["region"],
                "model": cfg["expected_model_id"], "live_test": True,
                "prompt_version": prompt_version, "schema_version": "1.0", "wire_version": contract,
                "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                "schema_sha256": hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest(),
                "wire_schema_sha256": hashlib.sha256(json.dumps(wire_schema, sort_keys=True).encode()).hexdigest(),
                "sent_image_sha256": hashlib.sha256(image).hexdigest()}
            if response_format is not None:
                self._local.metadata["response_format"] = response_format
                self._local.metadata["server_schema_sha256"] = hashlib.sha256(
                    json.dumps(_server_schema(cfg), sort_keys=True).encode()).hexdigest()
            model_start = time.perf_counter()
            try:
                result = client.chat(details, retry_strategy=oci.retry.NoneRetryStrategy(),
                    opc_retry_token=token, opc_request_id="kk-" + uuid.uuid4().hex)
            finally:
                self._local.metadata["model_call_seconds"] = time.perf_counter() - model_start
            self._local.metadata.update(_header_metadata(result.headers))
            response = oci.util.to_dict(result.data)
            self._local.metadata["response"] = response_metadata(response, cfg["model_family"])
            self._local.metadata["elapsed_seconds"] = time.perf_counter() - start
            if diagnostic_stem is not None:
                _write_diagnostic(diagnostic_stem + ".response.json", {"response": response, "metadata": self._local.metadata})
                diagnostic_captured = True
                self._local.metadata["diagnostic_response_captured"] = True
            if self._local.metadata.get("served_region_invalid"):
                raise AdapterError("MODEL_SERVED_REGION_INVALID")
            payload, _ = extract_response(response, cfg["model_family"])
            if contract in (COMPACT_WIRE_VERSION, NAMED_WIRE_VERSION):
                try:
                    payload = expand_named_response(payload) if contract == NAMED_WIRE_VERSION else expand_response(payload)
                except PayloadError as exc:
                    self._local.metadata["validation_reason"] = _compact_validation_reason(exc)
                    raise AdapterError("MODEL_NAMED_SCHEMA_INVALID" if contract == NAMED_WIRE_VERSION else "MODEL_COMPACT_SCHEMA_INVALID") from None
            return payload
        except AdapterError as exc:
            if self._local.metadata:
                self._local.metadata["error_code"] = exc.code
            raise
        except Exception as exc:
            if self._local.metadata:
                self._local.metadata["error_code"] = "OCI_CALL_FAILED"
            status = getattr(exc, "status", None)
            raise AdapterError("OCI_CALL_FAILED", retryable=status in (429, 500, 502, 503, 504)) from exc
        finally:
            if self._local.metadata:
                self._local.metadata["elapsed_seconds"] = time.perf_counter() - start
                if diagnostic_captured:
                    try:
                        _write_diagnostic(diagnostic_stem + ".metadata.json", {"metadata": self._local.metadata})
                    except Exception:
                        # Preserve the extraction error and the already-fsynced raw
                        # response if writing the optional outcome sidecar fails.
                        self._local.metadata["diagnostic_metadata_status"] = "WRITE_FAILED"
