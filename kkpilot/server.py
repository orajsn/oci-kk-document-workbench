"""Loopback-only HTTP interface for the local pilot, with same-origin checks."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import secrets
import socket
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .config import Config
from .repository import DomainError, Repository, STATES
from .validation import EVIDENCE_STATUSES, HOUSEHOLD_FIELDS, MAX_EXTRACTION_BYTES, MEMBER_FIELDS, ValidationError, inspect_image
from .worker import WorkerPool


JOB_ROUTE = re.compile(r"/api/jobs/([0-9a-f]{32})(?:/(image|retry|review|export))?")
STATIC_FILES = {"/": "index.html", "/index.html": "index.html", "/styles.css": "styles.css", "/app.js": "app.js", "/favicon.svg": "favicon.svg"}


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON keys are not supported.")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ValueError("Non-finite JSON values are not supported.")


class PilotServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    allow_reuse_address = True
    request_queue_size = 32

    def __init__(self, config: Config, *, extractor_factory=None, start_workers: bool = True):
        self.config = config
        config.data_dir.mkdir(parents=True, exist_ok=True)
        config.image_dir.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            config.data_dir.chmod(0o700)
            config.image_dir.chmod(0o700)
        self.repository = Repository(config.database_path)
        self.repository.initialize()
        self.csrf_token = secrets.token_urlsafe(32)
        self._request_slots = threading.BoundedSemaphore(32)
        self.workers = WorkerPool(self.repository, config, extractor_factory)
        super().__init__((config.host, config.port), PilotHandler)
        if start_workers:
            self.workers.start()

    @property
    def allowed_hosts(self) -> set[str]:
        port = self.server_address[1]
        hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if port == 80:
            hosts.update(("127.0.0.1", "localhost"))
        return hosts

    def process_request(self, request, client_address) -> None:
        if not self._request_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._request_slots.release()
            raise

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._request_slots.release()

    def handle_error(self, request, client_address) -> None:
        # Base implementation emits stack traces and potentially source text.
        pass

    def server_close(self) -> None:
        self.workers.stop()
        super().server_close()


class PilotHandler(BaseHTTPRequestHandler):
    server_version = "KKPilot"
    sys_version = ""
    protocol_version = "HTTP/1.0"

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, format: str, *args) -> None:
        # URL queries, identifiers, image names and payloads are private data.
        pass

    def _headers(self, status: int, content_type: str, length: int, *, filename: str | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()

    def _json(self, status: int, payload: dict, *, filename: str | None = None) -> None:
        data = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        self._headers(status, "application/json; charset=utf-8", len(data), filename=filename)
        self.wfile.write(data)

    def _error(self, code: str, message: str, status: int = 400) -> None:
        self._json(status, {"error": {"code": code, "message": message}})

    def _guard(self, mutation: bool = False) -> None:
        hosts = self.headers.get_all("Host", [])
        if len(hosts) != 1 or hosts[0].lower() not in self.server.allowed_hosts:
            raise DomainError("INVALID_HOST", "Use this pilot through its localhost address.", 403)
        host = hosts[0].lower()
        origin = self.headers.get("Origin")
        if origin is not None and origin != "http://" + host:
            raise DomainError("INVALID_ORIGIN", "Cross-origin requests are not allowed.", 403)
        public_navigation = (
            not mutation and self.command == "GET" and urlsplit(self.path).path in ("/", "/index.html")
            and self.headers.get("Sec-Fetch-Mode") == "navigate"
            and self.headers.get("Sec-Fetch-Dest") == "document" and origin is None
        )
        if self.headers.get("Sec-Fetch-Site") not in (None, "same-origin", "none") and not public_navigation:
            raise DomainError("INVALID_ORIGIN", "Cross-origin requests are not allowed.", 403)
        if mutation:
            token = self.headers.get("X-KK-Pilot-Token", "")
            if not secrets.compare_digest(token.encode("utf-8"), self.server.csrf_token.encode("ascii")):
                raise DomainError("INVALID_TOKEN", "Reload this local pilot page before submitting.", 403)
            if not origin and self.headers.get("Sec-Fetch-Site") is not None:
                # Browser mutations carry an Origin; reject opaque or stripped origins.
                raise DomainError("INVALID_ORIGIN", "An origin is required for browser submissions.", 403)

    def _read_json(self, maximum: int) -> dict:
        if self.headers.get("Transfer-Encoding") is not None:
            raise DomainError("INVALID_REQUEST", "Transfer encoding is not supported.")
        if self.headers.get("Content-Encoding", "identity") != "identity":
            raise DomainError("INVALID_REQUEST", "Encoded request bodies are not supported.")
        if self.headers.get_content_type() != "application/json":
            raise DomainError("CONTENT_TYPE", "Content-Type must be application/json.", 415)
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1 or not re.fullmatch(r"[0-9]+", lengths[0]):
            raise DomainError("CONTENT_LENGTH", "One valid Content-Length is required.", 411)
        length = int(lengths[0])
        if length > maximum:
            raise DomainError("UPLOAD_TOO_LARGE", "Request exceeds the configured size limit.", 413)
        body = self.rfile.read(length)
        if len(body) != length:
            raise DomainError("INVALID_JSON", "Request body is incomplete.")
        try:
            payload = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
        except (UnicodeError, ValueError, RecursionError):
            raise DomainError("INVALID_JSON", "Request must contain valid JSON with unique keys.") from None
        if not isinstance(payload, dict):
            raise DomainError("INVALID_JSON", "Request must contain a JSON object.")
        return payload

    def _route(self):
        parsed = urlsplit(self.path)
        if not self.path.startswith("/") or parsed.scheme or parsed.netloc or parsed.fragment:
            raise DomainError("INVALID_PATH", "Invalid request target.")
        return parsed.path, parse_qs(parsed.query, max_num_fields=10)

    def do_GET(self) -> None:
        self._dispatch(False)

    def do_POST(self) -> None:
        self._dispatch(True)

    def do_OPTIONS(self) -> None:
        self._error("METHOD_NOT_ALLOWED", "Cross-origin access is not supported.", 405)

    def _dispatch(self, mutation: bool) -> None:
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
        except (socket.timeout, TimeoutError):
            self._error("REQUEST_TIMEOUT", "The local request timed out.", 408)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except ValueError:
            self._error("INVALID_REQUEST", "The request is invalid.")
        except Exception:
            self._error("INTERNAL_ERROR", "The local request could not be completed. Retry after checking the local service.", 500)

    def _get(self, path: str, query: dict) -> None:
        config = self.server.config
        if path == "/api/health":
            self._json(200, {"status": "ok", "local_only": True, "live_oci_enabled": config.live_oci_enabled, "workers": config.workers, "modes": config.modes()})
        elif path == "/api/config":
            self._json(200, {
                "csrf_token": self.server.csrf_token, "modes": config.modes(), "max_upload_bytes": config.max_upload_bytes,
                "max_extraction_bytes": MAX_EXTRACTION_BYTES,
                "accepted_mime_types": ["image/jpeg", "image/png"], "household_fields": HOUSEHOLD_FIELDS,
                "member_fields": MEMBER_FIELDS, "evidence_statuses": EVIDENCE_STATUSES,
                "statuses": STATES, "local_only": True, "live_oci_enabled": config.live_oci_enabled,
                "validation_rules": ["No automatic acceptance.", "Raw identifiers remain strings; never infer digits.", "Resolve validation errors and unreviewed fields before acceptance.", "Synthetic demo and reference replay are not AI extraction."],
            })
        elif path == "/api/jobs":
            self._json(200, {"jobs": self.server.repository.list_jobs(query.get("status", [None])[0], query.get("query", [None])[0])})
        elif match := JOB_ROUTE.fullmatch(path):
            job_id, action = match.groups()
            if action is None:
                self._json(200, self.server.repository.get_job(job_id))
            elif action == "export":
                self._json(200, self.server.repository.export_job(job_id), filename=f"kk-pilot-{job_id}.json")
            elif action == "image":
                record = self.server.repository.image_record(job_id)
                image_path = (config.image_dir / record["image_relpath"]).resolve()
                if not image_path.is_relative_to(config.image_dir.resolve()) or not image_path.is_file():
                    raise DomainError("SOURCE_UNAVAILABLE", "Source image is unavailable.", 404)
                if image_path.stat().st_size > config.max_upload_bytes:
                    raise DomainError("SOURCE_CHANGED", "Source image integrity check failed.", 409)
                with image_path.open("rb") as source:
                    data = source.read(config.max_upload_bytes + 1)
                if len(data) > config.max_upload_bytes or hashlib.sha256(data).hexdigest() != record["sha256"]:
                    raise DomainError("SOURCE_CHANGED", "Source image integrity check failed.", 409)
                mime = "image/png" if image_path.suffix == ".png" else "image/jpeg"
                self._headers(200, mime, len(data))
                self.wfile.write(data)
            else:
                raise DomainError("METHOD_NOT_ALLOWED", "This action requires POST.", 405)
        elif path in STATIC_FILES:
            asset = (config.web_dir / STATIC_FILES[path]).resolve()
            if not asset.is_relative_to(config.web_dir) or not asset.is_file():
                raise DomainError("NOT_FOUND", "Page was not found.", 404)
            data = asset.read_bytes()
            mime = {".js": "text/javascript", ".css": "text/css", ".html": "text/html", ".svg": "image/svg+xml"}.get(asset.suffix, "application/octet-stream")
            self._headers(200, mime + "; charset=utf-8", len(data))
            self.wfile.write(data)
        else:
            raise DomainError("NOT_FOUND", "Endpoint was not found.", 404)

    def _post(self, path: str) -> None:
        if path == "/api/jobs":
            self._upload()
            return
        match = JOB_ROUTE.fullmatch(path)
        if match is None or match[2] not in ("retry", "review"):
            raise DomainError("NOT_FOUND", "Endpoint was not found.", 404)
        job_id, action = match.groups()
        # Allow escaped Unicode and formatting above the canonical extraction limit.
        payload = self._read_json(4 * MAX_EXTRACTION_BYTES + 65536)
        if action == "retry":
            if payload:
                raise DomainError("INVALID_REQUEST", "Retry expects an empty JSON object.")
            result = self.server.repository.retry_job(job_id)
            self.server.workers.notify()
        else:
            if set(payload) != {"expected_version", "decision", "extraction", "comment"}:
                raise DomainError("INVALID_REQUEST", "Review requires expected_version, decision, extraction and comment.")
            result = self.server.repository.review_job(job_id, payload["expected_version"], payload["decision"], payload["extraction"], {"issues": []}, payload["comment"])
        self._json(200, result)

    def _upload(self) -> None:
        config = self.server.config
        payload = self._read_json(((config.max_upload_bytes + 2) // 3) * 4 + 8192)
        if set(payload) != {"filename", "content_base64", "case_id", "idempotency_key", "mode"}:
            raise DomainError("INVALID_REQUEST", "Upload requires filename, content_base64, case_id, idempotency_key and mode.")
        for key, maximum in (("filename", 255), ("case_id", 128), ("idempotency_key", 128)):
            value = payload[key]
            if not isinstance(value, str) or not value.strip() or len(value) > maximum or any(ord(character) < 32 for character in value):
                raise DomainError("INVALID_REQUEST", f"{key} must be nonempty text up to {maximum} characters without control characters.")
        mode = payload["mode"]
        if not isinstance(mode, str) or mode not in ("demo", "reference", "oci"):
            raise DomainError("MODE_DISABLED", "Explicitly select an available extraction mode.")
        if mode == "oci" and not config.live_oci_enabled:
            raise DomainError("MODE_DISABLED", "Live OCI requires an explicit approved local configuration.")
        if not any(item["id"] == mode and item["available"] for item in config.modes()):
            raise DomainError("MODE_UNAVAILABLE", "This mode requires an explicitly configured local reference directory.", 409)
        encoded = payload["content_base64"]
        if not isinstance(encoded, str) or len(encoded) > ((config.max_upload_bytes + 2) // 3) * 4:
            raise DomainError("UPLOAD_TOO_LARGE", "Image exceeds the configured upload limit.", 413)
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            raise DomainError("INVALID_BASE64", "Image content must be valid base64.") from None
        if len(data) > config.max_upload_bytes:
            raise DomainError("UPLOAD_TOO_LARGE", "Image exceeds the configured upload limit.", 413)
        _, extension, _, _ = inspect_image(data, config.max_pixels)
        sha256 = hashlib.sha256(data).hexdigest()
        if mode == "oci" and not config.approved_image(sha256):
            raise DomainError("SOURCE_NOT_APPROVED", "This source image is not included in the configured OCI approval scope.", 403)
        identity = json.dumps({"sha256": sha256, "case_id": payload["case_id"], "filename": payload["filename"], "mode": mode}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        request_hash = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        stored_name = uuid.uuid4().hex + extension
        image_path = config.image_dir / stored_name
        temporary = config.image_dir / (stored_name + ".part")
        keep_image = False
        try:
            with temporary.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, image_path)
            result, created = self.server.repository.create_job(
                case_id=payload["case_id"], filename=payload["filename"], mode=mode, image_relpath=stored_name,
                sha256=sha256, idempotency_key=payload["idempotency_key"], request_hash=request_hash, max_attempts=config.max_attempts,
            )
            keep_image = created
        finally:
            if temporary.exists():
                temporary.unlink()
            if not keep_image and image_path.exists():
                image_path.unlink()
        self.server.workers.notify()
        self._json(201 if created else 200, result)


def serve(config: Config) -> None:
    server = PilotServer(config)
    mode_note = "approved OCI samples enabled" if config.live_oci_enabled else "demo/reference only; not AI extraction"
    print(f"Local KK pilot: http://127.0.0.1:{server.server_address[1]} ({mode_note})", flush=True)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
