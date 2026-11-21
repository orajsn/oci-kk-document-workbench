"""Scoped Gemini Pro image-only pilot adapter; imports never invoke OCI.

Uses the tested image and full named-field contract. Google Americas processing
must be explicitly approved. Local OCR is optional and disabled by default.
"""
from __future__ import annotations

import os
import base64
import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
from pathlib import Path
import re
import threading
import time
import uuid

from .errors import AdapterError
from . import gemini_contract as contract
from . import gemini_focused_contract as focused
from . import focused_source_views
from . import local_paddle_ocr, local_ocr_context, corroborated_ocr_policy, field_corroboration, grid_angle, nik_skew_policy
from . import three_way_nik_policy, three_way_du_evidence
from .oci_genai import (
    _diagnostic_enabled, _diagnostic_preflight, _write_diagnostic,
    _header_metadata, response_metadata,
)
from .oci_hybrid import _normalized
from ..validation import assert_valid_payload, PayloadError

PIPELINE = "gemini_image_only"
CONTRACT = "gemini-image-only-1.0"
MODEL = "google.gemini-2.5-pro"
ENTRY = "ap-batam-1"
SERVING = "us-chicago-1"
COMPARTMENT = os.environ.get("KK_OCI_COMPARTMENT_OCID", "")
PROFILE = os.environ.get("KK_OCI_ROUTING_PROFILE_OCID", "")
ENDPOINT = "https://inference.generativeai.ap-batam-1.oci.oraclecloud.com"
MANAGEMENT_ENDPOINT = "https://generativeai.ap-batam-1.oci.oraclecloud.com"


def _need(condition, code):
    if not condition:
        raise AdapterError(code)


def check_approval(config):
    _need(isinstance(config, dict), "APPROVAL_SCOPE_REQUIRED")
    _need(all(config.get(key) is True for key in (
        "allow_live_calls", "data_processing_authorized", "spending_authorized",
        "vision_api_compatibility_verified", "google_americas_processing_authorized",
    )), "LIVE_APPROVAL_REQUIRED")
    _need(all(isinstance(config.get(key), str) and 0 < len(config[key].strip()) <= 500
              for key in ("authorization_reference", "dataset_id")), "APPROVAL_SCOPE_REQUIRED")
    _need(config.get("pipeline") == PIPELINE and config.get("model_family") == "gemini"
          and config.get("extraction_contract") == CONTRACT
          and config.get("response_format") == "google_json_schema", "GEMINI_CONTRACT_REQUIRED")
    _need(config.get("expected_model_id") == MODEL, "GEMINI_MODEL_NOT_APPROVED")
    _need(config.get("region") == ENTRY and config.get("approved_serving_regions") == [SERVING],
          "GEMINI_REGION_NOT_APPROVED")
    _need(COMPARTMENT.startswith("ocid1.compartment.") and PROFILE.startswith("ocid1.generativeairoutingprofile.") and
          config.get("compartment_id") == COMPARTMENT and config.get("routing_profile_id") == PROFILE,
          "GEMINI_SCOPE_NOT_APPROVED")
    _need(config.get("external_processor") == "Google"
          and config.get("external_processing_scope") == "Google Americas", "GOOGLE_PROCESSING_SCOPE_REQUIRED")
    _need(config.get("auth") == "instance_principal", "GEMINI_INSTANCE_PRINCIPAL_REQUIRED")
    _need(type(config.get("max_requests")) is int and 1 <= config["max_requests"] <= 10000,
          "REQUEST_BUDGET_REQUIRED")
    enabled = config.get("focused_refinement_enabled", False)
    _need(type(enabled) is bool, "FOCUSED_CONFIG_INVALID")
    local = config.get('local_ocr', {})
    _need(type(local) is dict and type(local.get('enabled', False)) is bool, 'LOCAL_OCR_CONFIG_INVALID')
    ocr_enabled = local.get('enabled', False)
    field_enabled = config.get('field_corroboration_enabled', False)
    _need(type(field_enabled) is bool and (not field_enabled or ocr_enabled), 'FIELD_CORROBORATION_CONFIG_INVALID')
    alignment_enabled = config.get('nik_row_alignment_enabled', False)
    _need(type(alignment_enabled) is bool and (not alignment_enabled or ocr_enabled), 'ROW_ALIGNMENT_CONFIG_INVALID')
    three_way_enabled = config.get('three_way_nik_enabled', False)
    _need(type(three_way_enabled) is bool and (not three_way_enabled or (
        ocr_enabled and enabled and field_enabled and alignment_enabled
        and config.get('approved_document_service') is True)), 'THREE_WAY_NIK_CONFIG_INVALID')
    nik_manifest_keys = ('three_way_nik_manifest_path', 'three_way_nik_manifest_sha256')
    if three_way_enabled:
        path, digest = (config.get(k) for k in nik_manifest_keys)
        _need(type(path) is str and Path(path).is_absolute() and len(path) <= 1000
              and type(digest) is str and re.fullmatch(r'[0-9a-f]{64}', digest), 'THREE_WAY_NIK_CONFIG_INVALID')
    else:
        _need(not any(k in config for k in nik_manifest_keys), 'THREE_WAY_NIK_CONFIG_INVALID')
    _need(not ocr_enabled or enabled, 'LOCAL_OCR_REQUIRES_FOCUS')
    if ocr_enabled:
        _need(set(local) == {'enabled', 'model_manifest_path', 'model_manifest_sha256', 'timeout_seconds'}
              and type(local['model_manifest_path']) is str
              and Path(local['model_manifest_path']) == local_paddle_ocr.ROOT / 'model-manifest.json'
              and type(local['model_manifest_sha256']) is str
              and re.fullmatch(r'[0-9a-f]{64}', local['model_manifest_sha256'])
              and type(local['timeout_seconds']) is int and 1 <= local['timeout_seconds'] <= 60, 'LOCAL_OCR_CONFIG_INVALID')
        _need(_diagnostic_enabled(config), 'LOCAL_OCR_DIAGNOSTIC_REQUIRED')
    _need(config.get("maximum_provider_calls_per_job") == (5 if three_way_enabled else 3 if ocr_enabled else 2 if enabled else 1)
          and type(config.get("maximum_provider_calls_per_job")) is int, "SINGLE_MODEL_CALL_REQUIRED")
    if enabled:
        path, digest = config.get("focused_manifest_path"), config.get("focused_manifest_sha256")
        _need(isinstance(path, str) and Path(path).is_absolute() and len(path) <= 1000
              and isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest), "FOCUSED_CONFIG_INVALID")
    _need(config.get("max_output_tokens") == 8192 and type(config.get("max_output_tokens")) is int,
          "GEMINI_OUTPUT_LIMIT_REQUIRED")
    _need(config.get("max_image_edge", 4096) == 4096
          and type(config.get("max_image_edge", 4096)) is int, "GEMINI_IMAGE_PREPARATION_POLICY")
    hashes = config.get("approved_image_sha256s")
    _need(isinstance(hashes, list) and 1 <= len(hashes) <= 12
          and all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) for value in hashes)
          and len(set(hashes)) == len(hashes),
          "APPROVED_IMAGE_HASHES_REQUIRED")
    policies = config.get("preprocessing_by_sha256", {})
    _need(isinstance(policies, dict) and set(policies) <= set(hashes), "PREPROCESS_POLICY_SCOPE")
    for policy in policies.values():
        _need(isinstance(policy, dict) and set(policy) <= {"crop", "rotate"}, "PREPROCESS_POLICY_INVALID")
        crop, rotate = policy.get("crop"), policy.get("rotate", 0)
        _need(type(rotate) is int and rotate in (0, 90, 180, 270), "PREPROCESS_POLICY_INVALID")
        if crop is not None:
            _need(isinstance(crop, list) and len(crop) == 4 and all(type(v) is int and 0 <= v <= 100000 for v in crop)
                  and crop[0] < crop[2] and crop[1] < crop[3], "PREPROCESS_POLICY_INVALID")
    _need(type(config.get("max_upload_bytes", 10 * 1024 * 1024)) is int
          and 1024 <= config.get("max_upload_bytes", 10 * 1024 * 1024) <= 50 * 1024 * 1024,
          "IMAGE_TOO_LARGE")
    for key, default, upper in (("connect_timeout_seconds", 5, 10), ("read_timeout_seconds", 120, 120)):
        # Cloud lease validation must see the same explicit timeouts as these clients.
        _need(key in config, "GEMINI_TIMEOUT_EXPLICIT_REQUIRED")
        value = config[key]
        _need(type(value) in (int, float) and math.isfinite(value) and 0 < value <= upper, "TIMEOUT_INVALID")
    _diagnostic_enabled(config)


def verify_profile(profile, config):
    _need(isinstance(profile, dict) and profile.get("id") == PROFILE
          and profile.get("compartment_id") == COMPARTMENT
          and profile.get("lifecycle_state") == "ACTIVE"
          and (profile.get("region_routing_policy") or {}).get("allowed_regions") == [SERVING]
          and (profile.get("model_routing_policy") or {}).get("allowed_models") == [MODEL],
          "GEMINI_ROUTING_PROFILE_MISMATCH")


def build_chat_request(oci, config, image):
    models = oci.generative_ai_inference.models
    details = models.ChatDetails(
        compartment_id=COMPARTMENT,
        serving_mode=models.OnDemandServingMode(model_id=PROFILE),
        chat_request=models.GenericChatRequest(
            messages=[
                models.SystemMessage(content=[models.TextContent(text=contract.build_prompt())]),
                models.UserMessage(content=[models.ImageContent(image_url=models.ImageUrl(
                    url="data:image/jpeg;base64," + base64.b64encode(image).decode("ascii"), detail="AUTO"))]),
            ],
            max_tokens=8192, temperature=0, num_generations=1, is_stream=False,
            response_format=models.JsonSchemaResponseFormat(json_schema=models.ResponseJsonSchema(
                name="kk_extraction", schema=contract.google_response_schema(), is_strict=True)),
        ),
    )
    _need(len(contract.canonical(oci.util.to_dict(details))) < 8 * 1024 * 1024, "REQUEST_LIMIT")
    return details


def build_focused_request(oci, views):
    models = oci.generative_ai_inference.models
    content = [models.ImageContent(image_url=models.ImageUrl(
        url="data:image/png;base64," + base64.b64encode(view['bytes']).decode('ascii'), detail="AUTO"))
        for view in views]
    details = models.ChatDetails(
        compartment_id=COMPARTMENT, serving_mode=models.OnDemandServingMode(model_id=PROFILE),
        chat_request=models.GenericChatRequest(
            messages=[models.SystemMessage(content=[models.TextContent(text=focused.build_prompt(views))]),
                      models.UserMessage(content=content)],
            max_tokens=8192, temperature=0, num_generations=1, is_stream=False,
            response_format=models.JsonSchemaResponseFormat(json_schema=models.ResponseJsonSchema(
                name="kk_focused_identity_place", schema=focused.google_response_schema(), is_strict=True))))
    _need(len(contract.canonical(oci.util.to_dict(details))) < 8 * 1024 * 1024, "REQUEST_LIMIT")
    return details


def build_hybrid_request(oci, config, image, ocr_text):
    details = build_chat_request(oci, config, image)
    before = oci.util.to_dict(details)
    details.chat_request.messages[0].content[0].text += local_ocr_context.SYSTEM_SUFFIX
    item = oci.generative_ai_inference.models.TextContent(text=ocr_text)
    details.chat_request.messages[1].content.append(item)
    after = oci.util.to_dict(details)
    expected = copy.deepcopy(before)
    expected['chat_request']['messages'][0]['content'][0]['text'] += local_ocr_context.SYSTEM_SUFFIX
    expected['chat_request']['messages'][1]['content'].append(oci.util.to_dict(item))
    _need(contract.canonical(after) == contract.canonical(expected)
          and len(contract.canonical(after)) < 8 * 1024 * 1024, 'REQUEST_LIMIT')
    return details


def build_nik_request(oci, views):
    from . import population_nik_contract as nik
    models = oci.generative_ai_inference.models
    content = []
    for view in views:
        content.extend([models.TextContent(text='View: ' + view['name']),
                        models.ImageContent(image_url=models.ImageUrl(
                            url='data:image/png;base64,' + base64.b64encode(view['bytes']).decode('ascii'), detail='AUTO'))])
    details = models.ChatDetails(
        compartment_id=COMPARTMENT, serving_mode=models.OnDemandServingMode(model_id=PROFILE),
        chat_request=models.GenericChatRequest(
            messages=[models.SystemMessage(content=[models.TextContent(text=nik.build_prompt(views))]),
                      models.UserMessage(content=content)],
            max_tokens=8192, temperature=0, num_generations=1, is_stream=False,
            response_format=models.JsonSchemaResponseFormat(json_schema=models.ResponseJsonSchema(
                name='kk_field_geometry_or_date', schema=nik.google_response_schema(), is_strict=True))))
    _need(len(contract.canonical(oci.util.to_dict(details))) < 8 * 1024 * 1024, 'REQUEST_LIMIT')
    return details


def merge_focused(payload, wire):
    focused.validate_wire(wire)
    rows = {r['row_number']: r for r in wire['members']}
    if set(rows) != {r['row_number'] for r in payload['members']}:
        raise ValueError('row binding')
    result = copy.deepcopy(payload)
    changes = 0
    for member in result['members']:
        for field in ('full_name', 'birth_place'):
            value = rows[member['row_number']][field]
            changes += member[field] != value
            member[field] = value
    assert_valid_payload(result)
    return result, changes


def provider_failure_diagnostic(error, stage):
    """Content-free operator diagnostics; never a retry decision or raw error dump."""
    status = getattr(error, 'status', None)
    status = status if type(status) is int and 100 <= status <= 599 else None
    category = ('THROTTLED' if status == 429 else 'REQUEST_REJECTED' if status is not None and 400 <= status < 500
                else 'SERVICE_ERROR' if status is not None and status >= 500 else 'TRANSPORT_OR_UNKNOWN')
    result = {'stage': stage, 'http_status': status, 'category': category, 'automatic_retry': False}
    for attribute, output in (('code', 'service_code_sha256'), ('request_id', 'request_id_sha256')):
        value = getattr(error, attribute, None)
        if isinstance(value, str) and 0 < len(value) <= 4096:
            result[output] = hashlib.sha256(value.encode()).hexdigest()
    return result


class OciGeminiExtractor:
    def __init__(self, config, *, parallel_local_ocr=False, reusable_local_ocr=False):
        check_approval(config)
        _need(type(parallel_local_ocr) is bool, 'PARALLEL_OCR_FLAG_INVALID')
        _need(type(reusable_local_ocr) is bool, 'REUSABLE_OCR_FLAG_INVALID')
        self.config = copy.deepcopy(config)
        self._parallel_local_ocr = parallel_local_ocr
        self.requests = 0
        self._lock = threading.Lock()
        self._local = threading.local()
        self._attempts = set()
        self._clients = None
        self._reusable_local_ocr = reusable_local_ocr
        self._ocr_runtime = None
        if reusable_local_ocr:
            _need(isinstance(config.get('local_ocr'), dict) and config['local_ocr'].get('enabled') is True,
                  'REUSABLE_OCR_REQUIRES_LOCAL')
            from .reusable_paddle_ocr import ReusableOCR
            self._ocr_runtime = ReusableOCR(self.config['local_ocr'])

    @property
    def last_metadata(self):
        return getattr(self._local, "metadata", {})

    def _run_local_ocr(self, image, config, capture=None):
        runner = self._ocr_runtime.run if self._ocr_runtime is not None else local_paddle_ocr.run
        return runner(image, config) if capture is None else runner(image, config, capture)

    def _prefetch_ocr(self, image, config):
        started = time.perf_counter()
        try:
            raw = self._run_local_ocr(image, config)
            error = None
        except Exception as caught:
            raw, error = None, caught
        ended = time.perf_counter()
        return raw, error, started, ended

    def _start_ocr_prefetch(self, image):
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='kk-local-ocr')
        try:
            future = executor.submit(self._prefetch_ocr, image, copy.deepcopy(self.config['local_ocr']))
        except Exception:
            executor.shutdown(wait=True)
            raise
        self._local.ocr_prefetch = {'executor': executor, 'future': future,
                                   'image_sha256': hashlib.sha256(image).hexdigest(),
                                   'consumed': False, 'consume_started': None}

    def _consume_ocr(self, image, capture, metadata):
        pending = getattr(self._local, 'ocr_prefetch', None)
        if pending is None:
            started = time.perf_counter()
            try:
                return self._run_local_ocr(image, self.config['local_ocr'], capture)
            finally:
                metadata['local_ocr_seconds'] = time.perf_counter() - started
                if self._ocr_runtime is not None:
                    metadata.update(self._ocr_runtime.metadata_for(image))
        _need(not pending['consumed'] and pending['image_sha256'] == hashlib.sha256(image).hexdigest(),
              'OCR_PREFETCH_BINDING')
        pending['consumed'] = True
        pending['consume_started'] = time.perf_counter()
        raw, error, started, ended = pending['future'].result()
        if self._ocr_runtime is not None:
            metadata.update(self._ocr_runtime.metadata_for(image))
        metadata['local_ocr_seconds'] = ended - started
        metadata['local_ocr_wait_seconds'] = time.perf_counter() - pending['consume_started']
        metadata['local_ocr_overlap_seconds'] = max(0.0, min(ended, pending['consume_started']) - started)
        if error is not None:
            raise error
        capture(raw)
        return raw

    def _finish_ocr_prefetch(self, metadata):
        pending = getattr(self._local, 'ocr_prefetch', None)
        if pending is None:
            return
        try:
            # Paddle's existing subprocess timeout/kill guard still applies.
            # Drain on every exit so an unused OCR cannot leak into the next job.
            cleanup_started = time.perf_counter()
            pending['executor'].shutdown(wait=True)
            metadata['local_ocr_cleanup_wait_seconds'] = time.perf_counter() - cleanup_started
            _, error, started, ended = pending['future'].result()
            if self._ocr_runtime is not None:
                metadata.update(self._ocr_runtime.metadata_for_digest(pending['image_sha256']))
            metadata['local_ocr_seconds'] = ended - started
            metadata['local_ocr_prefetch_status'] = ('FAILED' if error is not None else
                                                   'CONSUMED' if pending['consumed'] else 'UNUSED')
            metadata.setdefault('local_ocr_wait_seconds', 0.0)
            metadata.setdefault('local_ocr_overlap_seconds', 0.0)
        finally:
            self._local.ocr_prefetch = None

    def _get_clients(self, oci):
        with self._lock:
            if self._clients is None:
                signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
                options = {"signer": signer, "retry_strategy": oci.retry.NoneRetryStrategy(),
                           "timeout": (self.config.get("connect_timeout_seconds", 5),
                                       self.config.get("read_timeout_seconds", 120))}
                management = oci.generative_ai.GenerativeAiClient(
                    {"region": ENTRY}, service_endpoint=MANAGEMENT_ENDPOINT, **options)
                inference = oci.generative_ai_inference.GenerativeAiInferenceClient(
                    {"region": ENTRY}, service_endpoint=ENDPOINT, **options)
                for client, expected in ((management, MANAGEMENT_ENDPOINT), (inference, ENDPOINT)):
                    _need(client.base_client.endpoint.rstrip("/").removesuffix("/20231130") == expected,
                          "GEMINI_ENDPOINT_MISMATCH")
                self._clients = management, inference
            return self._clients

    def extract(self, image_path: Path, context: dict):
        _need(getattr(self._local, 'ocr_prefetch', None) is None, 'OCR_PREFETCH_ALREADY_ACTIVE')
        self._local.metadata = {}
        check_approval(self.config)
        _need(isinstance(context, dict), "ATTEMPT_ID_INVALID")
        try:
            attempt = (uuid.UUID(str(context.get("job_id"))), uuid.UUID(str(context.get("attempt_id"))))
        except (ValueError, TypeError, AttributeError):
            raise AdapterError("ATTEMPT_ID_INVALID") from None
        limit = self.config.get("max_upload_bytes", 10 * 1024 * 1024)
        try:
            with Path(image_path).open("rb") as stream:
                source = stream.read(limit + 1)
        except OSError:
            raise AdapterError("SOURCE_READ_FAILED") from None
        _need(len(source) <= limit, "IMAGE_TOO_LARGE")
        source_hash = hashlib.sha256(source).hexdigest()
        _need(source_hash in self.config["approved_image_sha256s"] and context.get("sha256") == source_hash,
              "SOURCE_NOT_APPROVED")
        with self._lock:
            _need(attempt not in self._attempts, "ATTEMPT_ALREADY_RESERVED")
            _need(self.requests < self.config["max_requests"], "REQUEST_BUDGET_EXHAUSTED")
        stem = _diagnostic_preflight(self.config, context)
        captured = False
        start = time.perf_counter()
        try:
            # _normalized is pure image geometry/encoding. It creates no OCI or OCR client.
            image = _normalized(source, self.config.get("preprocessing_by_sha256", {}).get(source_hash, {}), limit)
            _need(isinstance(image, bytes) and image.startswith(b"\xff\xd8\xff")
                  and 0 < len(image) < 5 * 1024 * 1024, "PREPARED_IMAGE_INVALID")
        except AdapterError:
            raise
        except Exception:
            raise AdapterError("PREPROCESS_FAILED") from None
        metadata = self._local.metadata = {
            "mode": "oci", "pipeline": PIPELINE, "entry_region": ENTRY, "model": MODEL,
            "external_processor": "Google", "external_processing_scope": "Google Americas",
            "live_test": True, "prompt_version": CONTRACT, "schema_version": "1.0",
            "wire_version": CONTRACT, "source_sha256": source_hash,
            "sent_image_sha256": hashlib.sha256(image).hexdigest(),
            "prompt_sha256": hashlib.sha256(contract.build_prompt().encode("utf-8")).hexdigest(),
            "schema_sha256": hashlib.sha256(contract.canonical(contract.build_schema())).hexdigest(),
            "server_schema_sha256": hashlib.sha256(contract.canonical(contract.google_response_schema())).hexdigest(),
            "response_format": "google_json_schema", "response": {}, "ocr_used": False,
            "preparation_seconds": time.perf_counter() - start,
        }
        metadata.update(refinement_status=('FAILED' if self.config.get('focused_refinement_enabled', False) else 'OFF'),
                        provider_calls_attempted=0, refinement_model_call_seconds=0.0,
                        maximum_provider_calls_per_job=self.config['maximum_provider_calls_per_job'],
                        refinement_value_changes=0, refinement_contract=focused.VERSION)
        if self.config.get('local_ocr', {}).get('enabled', False):
            metadata.update(hybrid_status='HELD', local_ocr_seconds=0.0, hybrid_model_call_seconds=0.0,
                            hybrid_nik_accepted_count=0, hybrid_nik_held_count=0)
        if self.config.get('field_corroboration_enabled', False):
            metadata.update(field_corroboration_status='HELD', field_corroboration_changed_count=0,
                            field_corroboration_held_count=0, field_corroboration_unchanged_count=0)
        if self.config.get('nik_row_alignment_enabled', False):
            metadata.update(row_alignment_status='UNAVAILABLE', row_alignment_seconds=0.0,
                            row_alignment_method=grid_angle.VERSION)
        if self.config.get('three_way_nik_enabled', False):
            metadata.update(three_way_nik_status='HELD', three_way_nik_reason='PREREQUISITE_UNAVAILABLE',
                three_way_nik_seconds=0.0, three_way_nik_du_seconds=0.0, three_way_nik_model_call_seconds=0.0,
                three_way_nik_changed_count=0, three_way_nik_held_count=0, three_way_nik_unchanged_count=0,
                three_way_nik_du_calls_attempted=0, three_way_nik_model_calls_attempted=0)
        try:
            import oci
            management, client = self._get_clients(oci)
            profile = management.get_routing_profile(PROFILE, retry_strategy=oci.retry.NoneRetryStrategy())
            verify_profile(oci.util.to_dict(profile.data), self.config)
            details = build_chat_request(oci, self.config, image)
            with self._lock:
                _need(attempt not in self._attempts, "ATTEMPT_ALREADY_RESERVED")
                _need(self.requests < self.config["max_requests"], "REQUEST_BUDGET_EXHAUSTED")
                self._attempts.add(attempt)
                self.requests += 1
            if self._parallel_local_ocr and self.config.get('local_ocr', {}).get('enabled', False):
                self._start_ocr_prefetch(image)
            model_start = time.perf_counter()
            try:
                metadata['provider_calls_attempted'] = 1
                result = client.chat(details, retry_strategy=oci.retry.NoneRetryStrategy(),
                                     opc_request_id="kk-gemini-" + uuid.uuid4().hex)
            except Exception as error:
                metadata['provider_failure'] = provider_failure_diagnostic(error, 'primary')
                raise
            finally:
                metadata["model_call_seconds"] = time.perf_counter() - model_start
            metadata.update(_header_metadata(result.headers))
            raw = oci.util.to_dict(result.data)
            metadata["response"] = response_metadata(raw, "generic")
            if stem is not None:
                _write_diagnostic(stem + ".response.json", {"response": raw, "metadata": metadata})
                captured = True
                metadata["diagnostic_response_captured"] = True
            _need(result.status == 200 and metadata.get("served_region") == SERVING
                  and not metadata.get("served_region_invalid"), "GEMINI_RESPONSE_REGION_MISMATCH")
            _need(isinstance(raw, dict) and raw.get("model_id") in (MODEL, PROFILE), "GEMINI_RESPONSE_MODEL_MISMATCH")
            try:
                wire, representation = contract.extract_wire(raw)
                expanded = contract.expand_candidate(wire)
                payload = expanded["extraction"]
                assert_valid_payload(payload)
            except (contract.ContractError, PayloadError) as error:
                reason = str(error) if isinstance(error, contract.ContractError) else "DOMAIN_SHAPE"
                metadata["validation_reason"] = reason if re.fullmatch(r"[A-Z0-9_]{1,80}", reason) else "DOMAIN_SHAPE"
                raise AdapterError("MODEL_GEMINI_SCHEMA_INVALID") from None
            metadata["value_changes"] = 0
            metadata["json_fence_removed"] = representation["json_fence_removed"]
            if self.config.get('focused_refinement_enabled', False):
                payload = self._refine(oci, client, source, source_hash, payload, metadata, stem)
            if self.config.get('local_ocr', {}).get('enabled', False):
                if metadata['refinement_status'] == 'APPLIED':
                    payload = self._hybrid(oci, client, source, source_hash, image, payload, metadata, stem)
                else:
                    metadata['hybrid_status'] = 'SKIPPED_FOCUS'
            return payload
        except AdapterError as error:
            metadata["error_code"] = error.code
            raise
        except Exception:
            metadata["error_code"] = "GEMINI_CALL_FAILED"
            # Unknown provider outcomes are not replayed automatically by this adapter.
            raise AdapterError("GEMINI_CALL_FAILED", retryable=False) from None
        finally:
            self._finish_ocr_prefetch(metadata)
            metadata["elapsed_seconds"] = time.perf_counter() - start
            if captured or (stem is not None and 'provider_failure' in metadata):
                try:
                    _write_diagnostic(stem + ".metadata.json", {"metadata": metadata})
                except Exception:
                    metadata["diagnostic_metadata_status"] = "WRITE_FAILED"

    def _refine(self, oci, client, source, source_hash, payload, metadata, stem):
        try:
            views = focused_source_views.prepare(
                source, source_hash, self.config.get('preprocessing_by_sha256', {}).get(source_hash, {}),
                self.config['focused_manifest_path'], self.config['focused_manifest_sha256'])
            details = build_focused_request(oci, views)
        except Exception:
            metadata['refinement_status'] = 'GEOMETRY_UNAVAILABLE'
            return self._focus_fallback(payload)
        start = time.perf_counter()
        metadata['provider_calls_attempted'] = 2
        try:
            result = client.chat(details, retry_strategy=oci.retry.NoneRetryStrategy(),
                                 opc_request_id='kk-gemini-focus-' + uuid.uuid4().hex)
        except Exception as error:
            status = getattr(error, 'status', None)
            metadata['refinement_status'] = ('FAILED' if type(status) is int and 400 <= status < 500
                                             else 'UNKNOWN_OUTCOME')
            return self._focus_fallback(payload)
        finally:
            metadata['refinement_model_call_seconds'] = time.perf_counter() - start
        try:
            raw = oci.util.to_dict(result.data)
            headers = _header_metadata(result.headers)
            if stem is not None:
                _write_diagnostic(stem + '.focused.response.json', {'response': raw, 'metadata': headers})
            if (result.status != 200 or headers.get('served_region') != SERVING
                    or headers.get('served_region_invalid') or not isinstance(raw, dict)
                    or raw.get('model_id') not in (MODEL, PROFILE)):
                metadata['refinement_status'] = 'FAILED'
                return self._focus_fallback(payload)
            try:
                wire, _ = focused.extract_wire(raw)
            except focused.FocusedReadError as error:
                metadata['refinement_status'] = ('ROW_MISMATCH' if str(error) == 'FOCUSED_ROW_BINDING' else 'FAILED')
                return self._focus_fallback(payload)
            try:
                merged, changes = merge_focused(payload, wire)
            except (ValueError, PayloadError):
                metadata['refinement_status'] = 'ROW_MISMATCH'
                return self._focus_fallback(payload)
            metadata['refinement_status'] = 'APPLIED'
            metadata['refinement_value_changes'] = changes
            return merged
        except Exception:
            metadata['refinement_status'] = 'FAILED'
            return self._focus_fallback(payload)

    @staticmethod
    def _focus_fallback(payload):
        result = copy.deepcopy(payload)
        result.setdefault('review_flags', []).append({'path': 'document', 'code': 'FOCUSED_REFINEMENT_FALLBACK',
                                                      'message': 'Focused refinement was unavailable; review the source.'})
        assert_valid_payload(result)
        return result

    def _hybrid(self, oci, client, source, source_hash, image, payload, metadata, stem):
        def fallback():
            result = copy.deepcopy(payload)
            result.setdefault('review_flags', []).append({'path': 'document', 'code': 'OCR_REFINEMENT_FALLBACK',
                'message': 'OCR refinement was unavailable; review the source.'})
            assert_valid_payload(result)
            return result
        try:
            policy = self.config.get('preprocessing_by_sha256', {}).get(source_hash, {})
            geometry = local_paddle_ocr.geometry(source, source_hash, image, policy,
                self.config['focused_manifest_path'], self.config['focused_manifest_sha256'])
            def capture(raw):
                if stem is not None: _write_diagnostic(stem + '.ocr.raw.json', {'ocr': raw})
            ocr = self._consume_ocr(image, capture, metadata)
            metadata['ocr_used'] = True
            from PIL import Image
            import io
            with Image.open(io.BytesIO(image)) as im: width, height = im.size
            context = local_ocr_context.format_context(ocr, width, height, source_hash)
            details = build_hybrid_request(oci, self.config, image, context)
        except Exception:
            metadata['hybrid_status'] = 'HELD'
            return fallback()
        start = time.perf_counter()
        metadata['provider_calls_attempted'] = 3
        try:
            result = client.chat(details, retry_strategy=oci.retry.NoneRetryStrategy(),
                                 opc_request_id='kk-gemini-ocr-' + uuid.uuid4().hex)
        except Exception as error:
            status = getattr(error, 'status', None)
            metadata['hybrid_status'] = ('HELD' if type(status) is int and 400 <= status < 500 else 'UNKNOWN_OUTCOME')
            return fallback()
        finally:
            metadata['hybrid_model_call_seconds'] = time.perf_counter() - start
        try:
            raw = oci.util.to_dict(result.data); headers = _header_metadata(result.headers)
            if stem is not None:
                _write_diagnostic(stem + '.hybrid.response.json', {'response': raw, 'metadata': headers})
            _need(result.status == 200 and headers.get('served_region') == SERVING
                  and not headers.get('served_region_invalid') and isinstance(raw, dict)
                  and raw.get('model_id') in (MODEL, PROFILE), 'HYBRID_RESPONSE_INVALID')
            wire, _ = contract.extract_wire(raw)
            hybrid = contract.expand_candidate(wire)['extraction']
            assert_valid_payload(hybrid)
            lines = json.loads(context)['lines']
            alignment, slope = None, None
            if self.config.get('nik_row_alignment_enabled', False):
                alignment, slope = self._row_alignment(image, geometry, metadata)
                if slope is not None:
                    try:
                        result_payload, evidence = nik_skew_policy.project(payload, hybrid, lines, geometry, slope)
                    except Exception:
                        metadata['row_alignment_status'] = 'UNAVAILABLE'
                        metadata.pop('row_alignment_angle_degrees', None)
                        alignment = {'status': 'UNAVAILABLE', 'method': grid_angle.VERSION,
                                     'seconds': metadata['row_alignment_seconds']}
                        result_payload, evidence = corroborated_ocr_policy.project(payload, hybrid, lines, geometry)
                else:
                    result_payload, evidence = corroborated_ocr_policy.project(payload, hybrid, lines, geometry)
            else:
                result_payload, evidence = corroborated_ocr_policy.project(payload, hybrid, lines, geometry)
            assert_valid_payload(result_payload)
            metadata['hybrid_nik_accepted_count'] = sum(x['nik_status'] == 'ACCEPTED' for x in evidence)
            metadata['hybrid_nik_held_count'] = sum(x['nik_status'] == 'HELD' for x in evidence)
            metadata['hybrid_status'] = 'APPLIED'
            if self.config.get('field_corroboration_enabled', False):
                result_payload = self._field_corroborate(result_payload, hybrid, lines, evidence, metadata, stem, alignment)
            elif stem is not None:
                diagnostic = {'evidence': evidence}
                if alignment is not None: diagnostic['row_alignment'] = alignment
                _write_diagnostic(stem + '.hybrid.evidence.json', diagnostic)
            if self.config.get('three_way_nik_enabled', False):
                result_payload = self._three_way_nik(oci, client, source, source_hash, image,
                    result_payload, lines, geometry, slope, metadata, stem)
            return result_payload
        except Exception:
            metadata['hybrid_status'] = 'HELD'
            return fallback()

    def _get_du_client(self, oci):
        # Created only for the explicitly enabled stage; no subscriptions or region changes.
        return oci.ai_document.AIServiceDocumentClient(
            {'region': ENTRY}, service_endpoint='https://document.aiservice.ap-batam-1.oci.oraclecloud.com',
            signer=oci.auth.signers.InstancePrincipalsSecurityTokenSigner(),
            retry_strategy=oci.retry.NoneRetryStrategy(),
            timeout=(self.config.get('connect_timeout_seconds', 5), self.config.get('read_timeout_seconds', 120)))

    def _three_way_nik(self, oci, client, source, source_hash, image, base, paddle, geometry, slope, metadata, stem):
        """Optional fail-closed pilot stage. Never receives references or old reads."""
        started = time.perf_counter(); evidence = []
        metadata.update(three_way_nik_status='HELD', three_way_nik_reason='PREREQUISITE_UNAVAILABLE',
            three_way_nik_du_calls_attempted=0, three_way_nik_model_calls_attempted=0,
            three_way_nik_du_seconds=0.0, three_way_nik_model_call_seconds=0.0,
            three_way_nik_changed_count=0, three_way_nik_held_count=len(base.get('members', [])),
            three_way_nik_unchanged_count=0)
        try:
            if stem is None:
                metadata['three_way_nik_reason'] = 'DIAGNOSTIC_UNAVAILABLE'
                return base
            if (geometry.get('mapping') != 'RESIZE_ONLY' or slope is None
                    or metadata.get('row_alignment_status') != 'AVAILABLE'
                    or metadata.get('field_corroboration_status') not in ('APPLIED', 'HELD')):
                return base
            metadata['three_way_nik_reason'] = 'GEOMETRY_UNAVAILABLE'
            from . import nik_source_views, population_nik_contract as nik
            views, provenance = nik_source_views.prepare(source, source_hash,
                self.config.get('preprocessing_by_sha256', {}).get(source_hash, {}),
                self.config['three_way_nik_manifest_path'], self.config['three_way_nik_manifest_sha256'])
            # Preflight the precise independent all-row prompt before spending on DU.
            details = build_nik_request(oci, views)
            import io
            from PIL import Image
            with Image.open(io.BytesIO(image)) as prepared:
                width, height = prepared.size
            metadata['three_way_nik_reason'] = 'DU_UNAVAILABLE'
            du = self._get_du_client(oci); dm = oci.ai_document.models
            du_request = dm.AnalyzeDocumentDetails(compartment_id=COMPARTMENT,
                document=dm.InlineDocumentDetails(data=base64.b64encode(image).decode('ascii')),
                features=[dm.DocumentTextExtractionFeature(), dm.DocumentTableExtractionFeature()])
            t = time.perf_counter()
            try:
                metadata['provider_calls_attempted'] += 1
                metadata['three_way_nik_du_calls_attempted'] = 1
                response = du.analyze_document(du_request, retry_strategy=oci.retry.NoneRetryStrategy(),
                    opc_request_id='kk-nik-du-' + uuid.uuid4().hex)
            finally:
                metadata['three_way_nik_du_seconds'] = time.perf_counter() - t
            metadata['three_way_nik_reason'] = 'DU_RESPONSE_INVALID'
            raw = oci.util.to_dict(response.data)
            _write_diagnostic(stem + '.three-way-nik.du.response.json', {'response': raw,
                'source_sha256': source_hash, 'sent_image_sha256': hashlib.sha256(image).hexdigest()})
            _need(response.status == 200, 'DU_RESPONSE_INVALID')
            du_lines, du_cells = three_way_du_evidence.convert(raw, width, height)
            metadata['three_way_nik_reason'] = 'POLICY_UNAVAILABLE'
            dual, decisions = three_way_nik_policy.propose(base, du_lines, du_cells, paddle, geometry, slope)
            evidence = decisions
            if not any(e['status'] == 'PROPOSED' and e['reason'] == 'DU_PADDLE_CORROBORATED' for e in decisions):
                metadata.update(three_way_nik_status='NO_PROPOSAL', three_way_nik_reason='NO_ELIGIBLE_PROPOSAL',
                    three_way_nik_unchanged_count=sum(e['status'] == 'UNCHANGED' for e in decisions),
                    three_way_nik_held_count=sum(e['status'] != 'UNCHANGED' for e in decisions))
                return base
            metadata['three_way_nik_reason'] = 'NIK_CALL_UNAVAILABLE'
            t = time.perf_counter()
            try:
                metadata['provider_calls_attempted'] += 1
                metadata['three_way_nik_model_calls_attempted'] = 1
                response = client.chat(details, retry_strategy=oci.retry.NoneRetryStrategy(),
                    opc_request_id='kk-nik-read-' + uuid.uuid4().hex)
            finally:
                metadata['three_way_nik_model_call_seconds'] = time.perf_counter() - t
            metadata['three_way_nik_reason'] = 'NIK_RESPONSE_INVALID'
            raw = oci.util.to_dict(response.data); headers = _header_metadata(response.headers)
            _write_diagnostic(stem + '.three-way-nik.response.json', {'response': raw, 'metadata': headers})
            _need(response.status == 200 and headers.get('served_region') == SERVING
                  and not headers.get('served_region_invalid') and type(raw) is dict
                  and raw.get('model_id') in (MODEL, PROFILE), 'NIK_RESPONSE_INVALID')
            wire, _ = nik.extract_wire(raw)
            metadata['three_way_nik_reason'] = 'ROWSET_MISMATCH'
            projected, evidence = three_way_nik_policy.gate(base, dual, {'members': wire['members']}, decisions)
            assert_valid_payload(projected)
            # Persist the private evidence before allowing the projection to escape.
            metadata['three_way_nik_reason'] = 'DIAGNOSTIC_UNAVAILABLE'
            _write_diagnostic(stem + '.three-way-nik.evidence.json', {'evidence': evidence, 'source_views': provenance})
            metadata.update(three_way_nik_status='APPLIED', three_way_nik_reason='COMPLETE',
                three_way_nik_changed_count=sum(e['status'] == 'APPLIED' for e in evidence),
                three_way_nik_held_count=sum(e['status'] == 'HELD' for e in evidence),
                three_way_nik_unchanged_count=sum(e['status'] == 'UNCHANGED' for e in evidence))
            return projected
        except Exception:
            metadata['three_way_nik_status'] = 'HELD'
            return base
        finally:
            metadata['three_way_nik_seconds'] = time.perf_counter() - started
            if stem is not None and metadata['three_way_nik_status'] != 'APPLIED':
                try:
                    _write_diagnostic(stem + '.three-way-nik.evidence.json', {'evidence': evidence,
                        'status': metadata['three_way_nik_status'], 'reason': metadata['three_way_nik_reason']})
                except Exception:
                    metadata.update(three_way_nik_status='HELD', three_way_nik_reason='DIAGNOSTIC_UNAVAILABLE')

    @staticmethod
    def _row_alignment(image, geometry, metadata):
        started = time.perf_counter()
        status, slope, angle = 'UNAVAILABLE', None, None
        try:
            result = grid_angle.estimate(image)
            scale = geometry.get('scale') if isinstance(geometry, dict) else None
            if (result.get('status') == 'AVAILABLE' and geometry.get('mapping') == 'RESIZE_ONLY'
                    and type(scale) in (tuple, list) and len(scale) == 2
                    and all(type(v) in (int, float) and math.isfinite(v) and v > 0 for v in scale)
                    and type(result.get('angle_degrees')) in (int, float) and math.isfinite(result['angle_degrees'])
                    and abs(result['angle_degrees']) <= grid_angle.MAX_ANGLE
                    and type(result.get('slope')) in (int, float) and math.isfinite(result['slope'])):
                converted = result['slope'] * scale[1] / scale[0]
                if math.isfinite(converted):
                    status, slope, angle = 'AVAILABLE', converted, result['angle_degrees']
        except Exception:
            pass
        elapsed = time.perf_counter() - started
        metadata.update(row_alignment_status=status, row_alignment_seconds=elapsed,
                        row_alignment_method=grid_angle.VERSION)
        record = {'status': status, 'method': grid_angle.VERSION, 'seconds': elapsed}
        if angle is not None:
            metadata['row_alignment_angle_degrees'] = angle
            record['angle_degrees'] = angle
        return record, slope

    @staticmethod
    def _field_corroborate(base, hybrid, lines, hybrid_evidence, metadata, stem, alignment=None):
        try:
            projected, evidence = field_corroboration.project(base, hybrid, lines)
            assert_valid_payload(projected)
            # Only the approved three fields may differ from the preceding result.
            permitted = copy.deepcopy(base)
            for key in ('address', 'issued_date_raw'):
                permitted['household'][key] = projected['household'][key]
            _need(len(permitted['members']) == len(projected['members']), 'FIELD_CORROBORATION_INVALID')
            for index, row in enumerate(permitted['members']):
                row['birth_date_raw'] = projected['members'][index]['birth_date_raw']
            _need(contract.canonical(permitted) == contract.canonical(projected), 'FIELD_CORROBORATION_INVALID')
            _need(type(evidence) is list and len(evidence) <= 102 and all(
                type(item) is dict and item.get('status') in ('applied', 'hold')
                and type(item.get('reason')) is str
                and item.get('reason') not in ('invalid_input', 'rowset_mismatch') for item in evidence), 'FIELD_CORROBORATION_INVALID')
            changed = sum(item['status'] == 'applied' for item in evidence)
            unchanged = sum(item['status'] == 'hold' and item['reason'] == 'unchanged' for item in evidence)
            held = len(evidence) - changed - unchanged
            if stem is not None:
                diagnostic = {'evidence': hybrid_evidence, 'field_evidence': evidence}
                if alignment is not None: diagnostic['row_alignment'] = alignment
                _write_diagnostic(stem + '.hybrid.evidence.json', diagnostic)
            metadata.update(field_corroboration_status=('HELD' if held else 'APPLIED'),
                            field_corroboration_changed_count=changed, field_corroboration_held_count=held,
                            field_corroboration_unchanged_count=unchanged)
            return projected
        except Exception:
            metadata['field_corroboration_status'] = 'HELD'
            if stem is not None:
                try:
                    diagnostic = {'evidence': hybrid_evidence, 'field_evidence': [
                        {'path': '$', 'status': 'hold', 'reason': 'stage_failed', 'indices': []}]}
                    if alignment is not None: diagnostic['row_alignment'] = alignment
                    _write_diagnostic(stem + '.hybrid.evidence.json', diagnostic)
                except Exception: pass
            retained = copy.deepcopy(base)
            retained.setdefault('review_flags', []).append({'path': 'document', 'code': 'FIELD_CORROBORATION_FALLBACK',
                'message': 'Field corroboration was unavailable; review the source.'})
            assert_valid_payload(retained)
            return retained
