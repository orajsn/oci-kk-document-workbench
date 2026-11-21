"""Optional approved Batam OCR and routed vision extraction."""
from __future__ import annotations
import base64, copy, hashlib, json, re, threading, time, uuid
from pathlib import Path
from .errors import AdapterError
from .oci_genai import (verify_routing_profile, extract_response, response_metadata,
                        _diagnostic_enabled, _diagnostic_preflight, _write_diagnostic)
from .ocr_hybrid_contract import build_ocr_context, build_hybrid_prompt, build_hybrid_data, build_hybrid_schema, expand_hybrid_response
from .document_preprocess import prepare_document_views
from .preprocess import prepare_image

def check_approval(c):
    def need(ok, code):
        if not ok: raise AdapterError(code)
    need(all(c.get(k) is True for k in ('allow_live_calls','data_processing_authorized','spending_authorized','vision_api_compatibility_verified')), 'LIVE_APPROVAL_REQUIRED')
    need(all(isinstance(c.get(k), str) and 0 < len(c[k].strip()) <= 500 for k in ('authorization_reference','dataset_id')), 'APPROVAL_SCOPE_REQUIRED')
    need(c.get('pipeline') == 'document_genai_hybrid' and c.get('extraction_contract') == 'ocr-hybrid-short-1.0' and c.get('response_format') == 'cohere_json_schema', 'HYBRID_CONTRACT_REQUIRED')
    need(c.get('region') == 'ap-batam-1' and c.get('approved_serving_regions') == ['me-dubai-1'], 'HYBRID_REGION_REQUIRED')
    need(c.get('model_family') == 'cohere_v2' and c.get('expected_model_id') == 'cohere.command-a-vision', 'HYBRID_MODEL_REQUIRED')
    need(c.get('approved_document_service') is True, 'DOCUMENT_SERVICE_APPROVAL_REQUIRED')
    need(str(c.get('routing_profile_id','')).startswith('ocid1.') and str(c.get('compartment_id','')).startswith('ocid1.compartment.'), 'HYBRID_SCOPE_REQUIRED')
    need(type(c.get('max_requests')) is int and 1 <= c['max_requests'] <= 10000 and type(c.get('maximum_provider_calls_per_job')) is int and c['maximum_provider_calls_per_job'] == 2, 'HYBRID_BUDGET_REQUIRED')
    hashes = c.get('approved_image_sha256s')
    need(isinstance(hashes,list) and bool(hashes) and all(isinstance(x,str) and re.fullmatch(r'[0-9a-f]{64}',x) for x in hashes), 'APPROVED_IMAGE_HASHES_REQUIRED')
    need(c.get('auth') in ('instance_principal','config_file'), 'EXPLICIT_OCI_AUTH_REQUIRED')
    if c['auth'] == 'config_file': need(bool(c.get('oci_config_path')) and bool(c.get('oci_profile')), 'EXPLICIT_OCI_CONFIG_REQUIRED')
    _diagnostic_enabled(c)

def _normalized(content, policy, limit):
    crop, rotate = policy.get('crop'), policy.get('rotate', 0)
    views, meta = prepare_document_views(content, {'enabled':True,'auto_crop':True,'table_views':False,'max_edge':4096,'crop':crop,'rotate':rotate})
    if meta.get('status') == 'UNAVAILABLE' or not views: raise AdapterError('PREPROCESS_FAILED')
    names = [v.get('name') for v in views]
    if len(names) != len(set(names)): raise AdapterError('PREPROCESS_FAILED')
    if meta.get('status') == 'NEEDS_REVIEW' or names == ['original']:
        return prepare_image(content,max_bytes=limit,max_edge=4096,crop=crop,rotate=rotate)[0]
    if meta.get('status') != 'READY': raise AdapterError('PREPROCESS_FAILED')
    selected = 'document' if any(x.get('type') == 'perspective' for x in meta.get('transforms',[])) else 'context'
    matches = [v for v in views if v.get('name') == selected]
    if len(matches) != 1 or matches[0].get('mime') != 'image/jpeg' or not isinstance(matches[0].get('bytes'),bytes): raise AdapterError('PREPROCESS_FAILED')
    return matches[0]['bytes']

class OciHybridExtractor:
    def __init__(self, config):
        check_approval(config)
        self.config = copy.deepcopy(config); self.requests = 0
        self._lock = threading.Lock(); self._local = threading.local(); self._clients = None; self._attempts = set()
    @property
    def last_metadata(self): return getattr(self._local, 'metadata', {})
    def _get_clients(self, oci):
        with self._lock:
            if self._clients is None:
                c = self.config; cfg = {'region':'ap-batam-1'}
                if c['auth'] == 'instance_principal': auth = {'signer':oci.auth.signers.InstancePrincipalsSecurityTokenSigner()}
                else:
                    cfg = oci.config.from_file(c['oci_config_path'], c['oci_profile']); cfg['region'] = 'ap-batam-1'; auth = {}
                opts = dict(auth, retry_strategy=oci.retry.NoneRetryStrategy())
                mgmt = oci.generative_ai.GenerativeAiClient(cfg, timeout=(5,30), **opts)
                du = oci.ai_document.AIServiceDocumentClient(cfg, service_endpoint='https://document.aiservice.ap-batam-1.oci.oraclecloud.com', timeout=(5,90), **opts)
                gen = oci.generative_ai_inference.GenerativeAiInferenceClient(cfg, service_endpoint='https://inference.generativeai.ap-batam-1.oci.oraclecloud.com', timeout=(5,120), **opts)
                self._clients = (mgmt,du,gen)
            return self._clients

    def extract(self, image_path, context):
        start = time.perf_counter()
        self._local.metadata = {}; c = self.config; check_approval(c)
        diagnostic_stem = _diagnostic_preflight(c, context)
        diagnostic_captured = False
        try:
            job = uuid.UUID(str(context.get('job_id'))); attempt = uuid.UUID(str(context.get('attempt_id')))
        except (ValueError,TypeError,AttributeError): raise AdapterError('ATTEMPT_ID_INVALID') from None
        limit = c.get('max_upload_bytes',10*1024*1024)
        if type(limit) is not int or not 1024 <= limit <= 50*1024*1024: raise AdapterError('IMAGE_TOO_LARGE')
        try:
            with Path(image_path).open('rb') as f: source = f.read(limit+1)
        except OSError: raise AdapterError('SOURCE_READ_FAILED') from None
        digest = hashlib.sha256(source).hexdigest()
        if len(source)>limit or digest not in c['approved_image_sha256s'] or context.get('sha256') != digest: raise AdapterError('SOURCE_NOT_APPROVED')
        try:
            image = _normalized(source, (c.get('preprocessing_by_sha256') or {}).get(digest,{}), limit)
        except AdapterError:
            raise
        except Exception:
            raise AdapterError('PREPROCESS_FAILED') from None
        if 4*((len(image)+2)//3)+65536 > 8*1024*1024: raise AdapterError('REQUEST_LIMIT')
        with self._lock:
            if self.requests >= c['max_requests']: raise AdapterError('REQUEST_BUDGET_EXHAUSTED')
            if (job, attempt) in self._attempts: raise AdapterError('ATTEMPT_ALREADY_RESERVED')
            self._attempts.add((job, attempt))
            self.requests += 1
        self._local.metadata = {'mode':'oci','entry_region':'ap-batam-1','ocr_region':'ap-batam-1','model':c['expected_model_id'],'live_test':True,'prompt_version':'1.0','schema_version':'1.0','wire_version':'ocr-hybrid-short-1.0','sent_image_sha256':hashlib.sha256(image).hexdigest()}
        try:
            import oci
            mgmt,du,gen = self._get_clients(oci)
            profile = mgmt.get_routing_profile(c['routing_profile_id'],retry_strategy=oci.retry.NoneRetryStrategy())
            verify_routing_profile(oci.util.to_dict(profile.data), c)
            dm = oci.ai_document.models
            details = dm.AnalyzeDocumentDetails(compartment_id=c['compartment_id'],document=dm.InlineDocumentDetails(data=base64.b64encode(image).decode('ascii')),features=[dm.DocumentTextExtractionFeature(),dm.DocumentTableExtractionFeature()])
            t = time.perf_counter()
            try: result = du.analyze_document(details,retry_strategy=oci.retry.NoneRetryStrategy(),opc_request_id='kk-du-'+attempt.hex)
            finally: self._local.metadata['ocr_seconds'] = time.perf_counter()-t
            context_ocr = build_ocr_context(oci.util.to_dict(result.data))
            self._local.metadata['ocr_counts'] = {k:sum(len(p[k]) for p in context_ocr['pages']) for k in ('lines','words','tables')}
            self._local.metadata['ocr_counts']['pages'] = len(context_ocr['pages'])
            prompt, data, schema = build_hybrid_prompt(), build_hybrid_data(context_ocr), build_hybrid_schema()
            if 4*((len(image)+2)//3)+len(prompt.encode())+len(data.encode())+len(json.dumps(schema).encode())+65536 > 8*1024*1024: raise AdapterError('REQUEST_LIMIT')
            m = oci.generative_ai_inference.models
            uri = 'data:image/jpeg;base64,'+base64.b64encode(image).decode('ascii')
            chat = m.ChatDetails(compartment_id=c['compartment_id'],serving_mode=m.OnDemandServingMode(model_id=c['routing_profile_id']),chat_request=m.CohereChatRequestV2(messages=[m.CohereSystemMessageV2(content=[m.CohereTextContentV2(text=prompt)]),m.CohereUserMessageV2(content=[m.CohereTextContentV2(text=data),m.CohereImageContentV2(image_url=m.CohereImageUrlV2(url=uri))])],max_tokens=3000,temperature=0,is_stream=False,response_format=m.CohereResponseJsonFormat(schema=schema)))
            t = time.perf_counter()
            try: result = gen.chat(chat,retry_strategy=oci.retry.NoneRetryStrategy(),opc_retry_token=str(uuid.uuid5(uuid.NAMESPACE_URL,str(job)+':'+str(attempt))),opc_request_id='kk-gen-'+attempt.hex)
            finally: self._local.metadata['model_call_seconds'] = time.perf_counter()-t
            raw = oci.util.to_dict(result.data); self._local.metadata['response'] = response_metadata(raw,'cohere_v2')
            if diagnostic_stem is not None:
                _write_diagnostic(diagnostic_stem + '.response.json', {'response':raw, 'metadata':self._local.metadata})
                diagnostic_captured = True
                self._local.metadata['diagnostic_response_captured'] = True
            if result.headers.get('x-genai-selected-region') != 'me-dubai-1': raise AdapterError('MODEL_SERVED_REGION_INVALID')
            self._local.metadata['served_region'] = 'me-dubai-1'
            wire,_ = extract_response(raw,'cohere_v2')
            return expand_hybrid_response(wire,context_ocr)[0]
        except AdapterError as exc:
            self._local.metadata['error_code'] = exc.code
            raise
        except Exception:
            self._local.metadata['error_code'] = 'OCI_CALL_FAILED'
            raise AdapterError('OCI_CALL_FAILED') from None
        finally:
            self._local.metadata['elapsed_seconds'] = time.perf_counter()-start
            if diagnostic_captured:
                try:
                    _write_diagnostic(diagnostic_stem + '.metadata.json', {'metadata':self._local.metadata})
                except Exception:
                    self._local.metadata['diagnostic_metadata_status'] = 'WRITE_FAILED'
