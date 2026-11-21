"""Explicitly selected extraction adapters. Importing this module never calls OCI."""
from .local import DemoExtractor, ReferenceExtractor
from .errors import AdapterError
import hashlib
import json
import threading

_live_cache = {}
_live_lock = threading.Lock()

def get_extractor(mode: str, config: dict | None = None):
    config = config or {}
    if mode == "demo":
        return DemoExtractor()
    if mode == "reference":
        return ReferenceExtractor(config.get("reference_dir"))
    if mode == "oci":
        if config.get("pipeline", "genai") == "gemini_image_only":
            from .oci_gemini import OciGeminiExtractor as Extractor
        elif config.get("pipeline", "genai") == "document_meta_multiview":
            from .oci_meta_hybrid import OciMetaHybridExtractor as Extractor
        elif config.get("pipeline", "genai") == "document_genai_hybrid":
            from .oci_hybrid import OciHybridExtractor as Extractor
        elif config.get("pipeline", "genai") == "genai":
            from .oci_genai import OciGenAIExtractor as Extractor
        else:
            raise AdapterError("PIPELINE_NOT_SUPPORTED")
        key = hashlib.sha256(json.dumps(config, sort_keys=True, default=str).encode()).hexdigest()
        with _live_lock:
            if key not in _live_cache:
                local_ocr = config.get('local_ocr')
                if (config.get('pipeline') == 'gemini_image_only' and isinstance(local_ocr, dict)
                        and local_ocr.get('enabled') is True):
                    _live_cache[key] = Extractor(config, parallel_local_ocr=True, reusable_local_ocr=True)
                else:
                    _live_cache[key] = Extractor(config)
            return _live_cache[key]
    raise AdapterError("MODE_NOT_SUPPORTED")
