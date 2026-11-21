"""Private cloud-pilot HTTP API, reachable only through an operator SSH tunnel.

The server uses Oracle repositories and versioned Object Storage. It never starts
the local SQLite worker. Constructing it requires an explicit execution gate and
validated scope; cloud clients are supplied by the separately guarded entrypoint.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import re
import secrets
import socket
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from . import nik_review
from .config import validate_config
from .contracts import CloudError
from ..repository import DomainError, STATES
from ..server import PilotHandler, STATIC_FILES
from ..validation import EVIDENCE_STATUSES, HOUSEHOLD_FIELDS, MAX_EXTRACTION_BYTES, MEMBER_FIELDS, ValidationError, inspect_image


_UUID = r"(?:[0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
JOB_ROUTE = re.compile(r"/api/jobs/(" + _UUID + r")(?:/(image|retry|review|export))?")
_HASH = re.compile(r"[0-9a-f]{64}")
_MODEL_NAMES = {
    "cohere.command-a-vision": "Cohere Command A Vision",
    "meta.llama-4-maverick-17b-128e-instruct-fp8": "Meta Llama 4 Maverick",
    "meta.llama-4-scout-17b-16e-instruct": "Meta Llama 4 Scout",
    "google.gemini-2.5-pro": "Gemini 2.5 Pro",
}


def validate_reviewer(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9@._+ -]{1,127}", value):
        raise CloudError("NAMED_SSH_OPERATOR_REQUIRED")
    return value


class CloudPilotServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    allow_reuse_address = True
    request_queue_size = 32

    def __init__(self, config: dict, workflow, review_repository, *, execute: bool = False,
                 host: str = "127.0.0.1", port: int = 8765, web_dir: Path | None = None):
        if execute is not True:
            raise CloudError("EXPLICIT_EXECUTE_REQUIRED")
        if host != "127.0.0.1" or type(port) is not int or not 0 <= port <= 65535:
            raise CloudError("LOOPBACK_BIND_REQUIRED")
        self.config = validate_config(config)
        if (workflow.cfg.get("config_sha256") != self.config["config_sha256"]
                or workflow.cfg.get("scope_id") != self.config["scope_id"]
                or review_repository.scope_id != self.config["scope_id"]
                or tuple(getattr(review_repository, "prior_review_scope_ids", ())) != tuple(self.config.get("prior_review_scope_ids", []))):
            raise CloudError("WEB_SCOPE_MISMATCH")
        self.reviewer_identity = validate_reviewer(review_repository.reviewer_identity)
        self.workflow, self.repository = workflow, review_repository
        self.objects = workflow.objects
        self.web_dir = (web_dir or Path(__file__).resolve().parents[2] / "web").resolve()
        self.csrf_token = secrets.token_urlsafe(32)
        self._request_slots = threading.BoundedSemaphore(32)
        super().__init__((host, port), CloudPilotHandler)

    @property
    def allowed_hosts(self):
        port = self.server_address[1]
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if port == 80:
            hosts.update(("127.0.0.1", "localhost"))
        return hosts

    def process_request(self, request, client_address):
        if not self._request_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._request_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._request_slots.release()

    def handle_error(self, request, client_address):
        # Do not emit request identifiers, payloads or credential-bearing traces.
        pass

    def runtime_config(self):
        cfg = self.config
        adapter = cfg["oci_adapter"]
        # The server constructor validates this deployment's exact model and
        # provider/location policy. Only this public allowlist is exposed.
        google = adapter.get("pipeline") == "gemini_image_only"
        return {
            "csrf_token": self.csrf_token, "runtime": "private_cloud", "local_only": False,
            "private_access": "ssh_tunnel", "live_oci_enabled": True,
            "modes": [{"id": "oci", "label": "OCI GenAI · approved samples", "available": True}],
            "max_upload_bytes": cfg["max_upload_bytes"], "max_extraction_bytes": MAX_EXTRACTION_BYTES,
            "accepted_mime_types": ["image/jpeg", "image/png"], "household_fields": HOUSEHOLD_FIELDS,
            "member_fields": MEMBER_FIELDS, "evidence_statuses": EVIDENCE_STATUSES,
            "statuses": STATES, "capabilities": {"manual_retry": False, "review": True, "export": True,
                                               "processing_status": True,
                                               "upload_image_check": cfg.get("upload_image_check_enabled") is True},
            "data_region": cfg["region"],
            "inference_regions": list(adapter["approved_serving_regions"]),
            "model_id": adapter["expected_model_id"],
            "model_display_name": _MODEL_NAMES.get(adapter["expected_model_id"], "OCI Generative AI"),
            "inference_provider": adapter["external_processor"] if google else "OCI",
            "external_processing_location": adapter["external_processing_scope"] if google else None,
            "local_ocr_enabled": (google and isinstance(adapter.get("local_ocr"), dict)
                                  and adapter["local_ocr"].get("enabled") is True),
            "reviewer_identity": self.reviewer_identity,
            "reviewer_identity_source": "configured_ssh_operator",
            "validation_rules": ["Only approved image hashes may be uploaded.", "Human review is required.",
                                 "Raw identifiers remain strings; never infer digits."],
        }


class CloudPilotHandler(PilotHandler):
    """Reuse the local UI's bounded JSON parser and same-origin response headers."""

    def _guard(self, mutation=False):
        for name in ("Origin", "X-KK-Pilot-Token", "Sec-Fetch-Site", "Sec-Fetch-Mode", "Sec-Fetch-Dest"):
            if len(self.headers.get_all(name, [])) > 1:
                raise DomainError("INVALID_REQUEST", "Repeated security headers are not allowed.", 403)
        super()._guard(mutation)

    def _dispatch(self, mutation):
        try:
            self._guard(mutation)
            path, query = self._route()
            if mutation:
                self._post(path)
            else:
                self._get(path, query)
        except DomainError as error:
            self._error(error.code, error.message, error.status)
        except ValidationError as error:
            self._error(error.code, error.message)
        except CloudError as error:
            code = error.code if re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", str(error.code)) else "CLOUD_REQUEST_FAILED"
            client_errors = {"CASE_ID_INVALID", "IDEMPOTENCY_KEY_INVALID", "FILENAME_INVALID", "IMAGE_INVALID", "IMAGE_SIZE_INVALID"}
            status = 403 if code == "SOURCE_NOT_APPROVED" else 409 if code == "IDEMPOTENCY_CONFLICT" else 400 if code in client_errors else 503
            message = "This image is outside the approved sample set." if code == "SOURCE_NOT_APPROVED" else "The cloud request could not be completed. Check the code with the pilot operator."
            self._error(code, message, status)
        except (socket.timeout, TimeoutError):
            self._error("REQUEST_TIMEOUT", "The private pilot request timed out.", 408)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except ValueError:
            self._error("INVALID_REQUEST", "The request is invalid.")
        except Exception:
            self._error("INTERNAL_ERROR", "The private pilot request could not be completed.", 500)

    def _get(self, path, query):
        if path in ("/nik-review", "/nik-review.js", "/nik-review.css"):
            asset_name, mime = {"/nik-review": ("nik-review.html","text/html"), "/nik-review.js": ("nik-review.js","text/javascript"), "/nik-review.css": ("nik-review.css","text/css")}[path]
            data = (self.server.web_dir / asset_name).read_bytes()
            self._headers(200, mime+"; charset=utf-8", len(data))
            self.wfile.write(data)
            return
        if path == "/api/nik-review":
            if query and (set(query)!={'job'} or len(query['job'])!=1 or not re.fullmatch(_UUID,query['job'][0])):
                raise DomainError('INVALID_QUERY','ID upload tidak valid.',400)
            self._json(200, {"cases": nik_review.listing(self.server.repository, query['job'][0] if query else None)})
            return
        route = re.fullmatch(r"/api/jobs/("+_UUID+r")/nik-routing",path)
        if route:
            self._json(200,nik_review.routing_status(self.server.repository,route[1]))
            return
        crop = re.fullmatch(r"/api/nik-review/([0-9a-f]{24})/(crop|context)", path)
        if crop:
            data = nik_review.image(self.server.repository, *crop.groups())
            self._headers(200, "image/png", len(data))
            self.wfile.write(data)
            return
        if path == "/api/health":
            self._json(200, {"status": "ok", "runtime": "private_cloud", "local_only": False,
                             "live_oci_enabled": True, "private_access": "ssh_tunnel"})
        elif path == "/api/config":
            self._json(200, self.server.runtime_config())
        elif path == "/api/processing-status":
            if query:
                raise DomainError("INVALID_QUERY", "Processing status uses the configured scope.")
            self._json(200, self.server.repository.processing_status())
        elif path == "/api/jobs":
            if set(query) - {"status", "query"} or any(len(values) != 1 for values in query.values()):
                raise DomainError("INVALID_QUERY", "Use one status and one search value.")
            self._json(200, {"jobs": self.server.repository.list_jobs(query.get("status", [None])[0], query.get("query", [None])[0])})
        elif match := JOB_ROUTE.fullmatch(path):
            job_id, action = match.groups()
            if action is None:
                self._json(200, self.server.repository.get_job(job_id))
            elif action == "export":
                self._json(200, self.server.repository.export_job(job_id), filename=f"kk-pilot-{job_id}.json")
            elif action == "image":
                self._image(job_id)
            else:
                raise DomainError("METHOD_NOT_ALLOWED", "This action requires POST.", 405)
        elif path in STATIC_FILES:
            asset = (self.server.web_dir / STATIC_FILES[path]).resolve()
            if not asset.is_relative_to(self.server.web_dir) or not asset.is_file():
                raise DomainError("NOT_FOUND", "Page was not found.", 404)
            data = asset.read_bytes()
            mime = {".js": "text/javascript", ".css": "text/css", ".html": "text/html", ".svg": "image/svg+xml"}.get(asset.suffix, "application/octet-stream")
            self._headers(200, mime + "; charset=utf-8", len(data))
            self.wfile.write(data)
        else:
            raise DomainError("NOT_FOUND", "Endpoint was not found.", 404)

    def _image(self, job_id):
        cfg = self.server.config
        record = self.server.repository.image_record(job_id)
        digest, name, version = record.get("sha256"), record.get("object_name"), record.get("object_version_id")
        length, mime = record.get("byte_length"), record.get("mime_type")
        extension = {"image/png": ".png", "image/jpeg": ".jpg"}.get(mime)
        if (not isinstance(digest, str) or not _HASH.fullmatch(digest)
                or digest not in cfg["oci_adapter"]["approved_image_sha256s"]
                or not extension or name != f"sources/{job_id}/{digest}{extension}"
                or not isinstance(version, str) or not version or len(version) > 256
                or type(length) is not int or not 1 <= length <= cfg["max_upload_bytes"]):
            raise DomainError("SOURCE_CHANGED", "Source image integrity check failed.", 409)
        data = self.server.objects.read_bounded(name, version, length)
        if not isinstance(data, bytes) or len(data) != length or hashlib.sha256(data).hexdigest() != digest:
            raise DomainError("SOURCE_CHANGED", "Source image integrity check failed.", 409)
        try:
            detected, actual_extension, _, _ = inspect_image(data, cfg.get("max_pixels", 40_000_000))
        except ValidationError:
            raise DomainError("SOURCE_CHANGED", "Source image integrity check failed.", 409) from None
        if detected != mime or actual_extension != extension:
            raise DomainError("SOURCE_CHANGED", "Source image integrity check failed.", 409)
        self._headers(200, mime, len(data))
        self.wfile.write(data)

    def _post(self, path):
        decision = re.fullmatch(r"/api/nik-review/([0-9a-f]{24})/decision", path)
        if decision:
            self._json(200, nik_review.decide(self.server.repository, decision[1], self._read_json(2048)))
            return
        if path == "/api/submission-status":
            payload = self._read_json(2048)
            if set(payload) != {"case_id", "idempotency_key"}:
                raise DomainError("INVALID_REQUEST", "Supply the case and original submission key.")
            job_id = self.server.workflow.submission_status(payload["case_id"], payload["idempotency_key"])
            self._json(200, {"job_id": job_id})
            return
        if path == "/api/image-check":
            self._image_check()
            return
        if path == "/api/jobs":
            self._upload()
            return
        match = JOB_ROUTE.fullmatch(path)
        if match is None or match[2] not in ("retry", "review"):
            raise DomainError("NOT_FOUND", "Endpoint was not found.", 404)
        job_id, action = match.groups()
        if action == "retry":
            raise DomainError("MANUAL_RETRY_UNAVAILABLE", "Manual retry is unavailable. Contact the pilot operator.", 405)
        payload = self._read_json(4 * MAX_EXTRACTION_BYTES + 65536)
        if set(payload) != {"expected_version", "decision", "extraction", "comment"}:
            raise DomainError("INVALID_REQUEST", "Review requires expected_version, decision, extraction and comment.")
        result = self.server.repository.review_job(job_id, payload["expected_version"], payload["decision"], payload["extraction"], payload["comment"])
        self._json(200, result)

    def _image_check(self):
        if self.server.config.get("upload_image_check_enabled") is not True:
            raise DomainError("IMAGE_CHECK_DISABLED", "Preparation preview is unavailable.", 503)
        maximum = self.server.config["max_upload_bytes"]
        payload = self._read_json(((maximum + 2) // 3) * 4 + 8192)
        if set(payload) != {"content_base64"}:
            raise DomainError("INVALID_REQUEST", "Image content is required.")
        encoded = payload["content_base64"]
        if not isinstance(encoded, str) or len(encoded) > ((maximum + 2) // 3) * 4:
            raise DomainError("UPLOAD_TOO_LARGE", "Image exceeds the configured upload limit.", 413)
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            raise DomainError("INVALID_BASE64", "Image content must be valid base64.") from None
        if len(data) > maximum:
            raise DomainError("UPLOAD_TOO_LARGE", "Image exceeds the configured upload limit.", 413)
        digest = hashlib.sha256(data).hexdigest()
        if digest not in self.server.workflow._approved_hashes:
            raise CloudError("SOURCE_NOT_APPROVED")
        from ..adapters.document_preprocess import prepare_document_views
        manual = (self.server.config["oci_adapter"].get("preprocessing_by_sha256") or {}).get(digest, {})
        policy = {"enabled": True, "auto_crop": True, "table_views": False,
                  "crop": manual.get("crop"), "rotate": manual.get("rotate", 0)}
        _, meta = prepare_document_views(data, policy)
        transforms = meta.get("transforms", [])
        reason = meta.get("reason")
        reasons = {
            "NO_CONFIDENT_BOUNDARY": "Document boundary could not be identified confidently.",
            "MULTIPLE_LARGE_BOUNDARIES": "More than one possible document boundary was found.",
            "BOUNDARY_NEAR_IMAGE_EDGE": "A possible boundary is close to the image edge.",
            "DEGENERATE_BOUNDARY": "The possible boundary needs manual review.",
            "IMPLAUSIBLE_GEOMETRY_OR_MARGIN": "The possible boundary needs manual review.",
            "TOTAL_BYTES_LIMIT": "The preparation check could not finish.",
            "PILLOW_MISSING": "The preparation check is unavailable.",
            "OPENCV_NUMPY_MISSING": "The boundary check is unavailable.",
        }
        self._json(200, {"width": meta["original_size"][0], "height": meta["original_size"][1],
                         "orientation": meta.get("exif_orientation"),
                         "document_boundary_available": any(t.get("type") in ("perspective", "manual_crop") for t in transforms),
                         "review_reasons": [reasons.get(reason, "The preparation check needs manual review.")] if reason else []})

    def _upload(self):
        maximum = self.server.config["max_upload_bytes"]
        payload = self._read_json(((maximum + 2) // 3) * 4 + 8192)
        if set(payload) != {"filename", "content_base64", "case_id", "idempotency_key", "mode"}:
            raise DomainError("INVALID_REQUEST", "Upload requires filename, content_base64, case_id, idempotency_key and mode.")
        if payload["mode"] != "oci":
            raise DomainError("MODE_DISABLED", "This private cloud pilot accepts only approved OCI samples.")
        encoded = payload["content_base64"]
        if not isinstance(encoded, str) or len(encoded) > ((maximum + 2) // 3) * 4:
            raise DomainError("UPLOAD_TOO_LARGE", "Image exceeds the configured upload limit.", 413)
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            raise DomainError("INVALID_BASE64", "Image content must be valid base64.") from None
        if len(data) > maximum:
            raise DomainError("UPLOAD_TOO_LARGE", "Image exceeds the configured upload limit.", 413)
        inspect_image(data, self.server.config.get("max_pixels", 40_000_000))
        saved = self.server.workflow.enqueue_bytes(data, payload["filename"], payload["case_id"], payload["idempotency_key"])
        # The browser consumes the same detail envelope used by GET and review.
        self._json(201 if saved["created"] else 200, self.server.repository.get_job(saved["job_id"]))
