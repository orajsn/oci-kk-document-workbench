"""Explicit, safe configuration for the loopback-only local pilot."""

from __future__ import annotations

import os
import copy
import json
import re
from dataclasses import dataclass, field
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parent.parent
MODE_LABELS = {
    "demo": "Synthetic demo · not AI",
    "reference": "Reference replay · not AI",
    "oci": "Live OCI · Approved Samples Only",
}


@dataclass(frozen=True)
class Config:
    data_dir: Path = PACKAGE_ROOT / "runtime"
    host: str = "127.0.0.1"
    port: int = 8765
    workers: int = 2
    max_upload_bytes: int = 10 * 1024 * 1024
    max_pixels: int = 40_000_000
    max_attempts: int = 3
    lease_seconds: float = 120.0
    retry_delay_seconds: float = 2.0
    reference_dir: Path | None = None
    oci_config_path: Path | None = field(default=None, repr=False)
    _oci_settings: dict = field(default_factory=dict, init=False, repr=False, compare=False)
    web_dir: Path = PACKAGE_ROOT / "web"

    def __post_init__(self) -> None:
        # No unsafe deployment escape hatch: a secure hosted service is separate work.
        if self.host != "127.0.0.1":
            raise ValueError("This local pilot only binds to 127.0.0.1.")
        if not 0 <= self.port <= 65535:
            raise ValueError("Port must be between 0 and 65535.")
        if not 1 <= self.workers <= 8:
            raise ValueError("Worker count must be between 1 and 8.")
        if not 1 <= self.max_attempts <= 10:
            raise ValueError("Maximum attempts must be between 1 and 10.")
        if not 1024 <= self.max_upload_bytes <= 50 * 1024 * 1024:
            raise ValueError("Upload limit must be between 1 KiB and 50 MiB.")
        if not 1 <= self.max_pixels <= 100_000_000:
            raise ValueError("Pixel limit is outside the supported range.")
        if self.lease_seconds < 0.1 or self.retry_delay_seconds < 0:
            raise ValueError("Invalid lease or retry delay.")
        object.__setattr__(self, "data_dir", Path(self.data_dir).resolve())
        object.__setattr__(self, "web_dir", Path(self.web_dir).resolve())
        if self.reference_dir is not None:
            object.__setattr__(self, "reference_dir", Path(self.reference_dir).resolve())
        if self.oci_config_path is not None:
            path = Path(self.oci_config_path).resolve()
            object.__setattr__(self, "oci_config_path", path)
            object.__setattr__(self, "_oci_settings", self._read_oci_settings(path))

    @staticmethod
    def _read_oci_settings(path: Path) -> dict:
        """Validate explicit approval JSON without reading credentials or calling OCI."""
        def unique_pairs(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate key")
                result[key] = value
            return result

        def invalid_constant(value):
            raise ValueError("nonfinite value")

        try:
            with path.open("rb") as stream:
                raw = stream.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ValueError("too large")
            settings = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique_pairs, parse_constant=invalid_constant)
            if not isinstance(settings, dict):
                raise ValueError("object required")
        except (OSError, UnicodeError, ValueError, RecursionError):
            raise ValueError("OCI approval configuration must be a valid JSON object of at most 1 MiB.") from None
        hashes = settings.get("approved_image_sha256s")
        if not isinstance(hashes, list) or not hashes or len(hashes) > 10000 or any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value) for value in hashes):
            raise ValueError("OCI approval configuration requires approved_image_sha256s containing explicit SHA-256 hashes.")
        from .adapters.oci_genai import check_approval
        from .adapters.errors import AdapterError
        try:
            check_approval(settings)
        except AdapterError as error:
            code = getattr(error, "code", "INVALID_APPROVAL")
            if not isinstance(code, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", code):
                code = "INVALID_APPROVAL"
            raise ValueError(f"OCI approval configuration failed its safety checks: {code}.") from None
        except (TypeError, ValueError, AttributeError):
            raise ValueError("OCI approval configuration failed its safety checks: INVALID_APPROVAL.") from None
        return settings

    @property
    def live_oci_enabled(self) -> bool:
        return bool(self._oci_settings)

    def approved_image(self, sha256: str) -> bool:
        return self.live_oci_enabled and sha256 in self._oci_settings["approved_image_sha256s"]

    @property
    def database_path(self) -> Path:
        return self.data_dir / "pilot.sqlite3"

    @property
    def image_dir(self) -> Path:
        return self.data_dir / "images"

    def modes(self) -> list[dict]:
        availability = {"demo": True, "reference": bool(self.reference_dir and self.reference_dir.is_dir()), "oci": self.live_oci_enabled}
        return [{"id": mode, "label": label, "available": availability[mode]} for mode, label in MODE_LABELS.items()]

    def adapter_config(self, mode: str | None = None) -> dict:
        # Never load an OCI profile or existing machine credentials implicitly.
        if mode == "oci":
            if not self.live_oci_enabled:
                raise ValueError("Live OCI extraction is not enabled.")
            return copy.deepcopy(self._oci_settings)
        return {"reference_dir": str(self.reference_dir) if self.reference_dir else None}


def from_environment(**overrides: object) -> Config:
    """Use only the pilot's explicitly named settings, never ambient OCI settings."""
    values: dict = {}
    settings = {
        "KK_PILOT_DATA_DIR": ("data_dir", Path),
        "KK_PILOT_HOST": ("host", str),
        "KK_PILOT_PORT": ("port", int),
        "KK_PILOT_WORKERS": ("workers", int),
        "KK_PILOT_MAX_UPLOAD_BYTES": ("max_upload_bytes", int),
        "KK_PILOT_REFERENCE_DIR": ("reference_dir", Path),
        "KK_PILOT_OCI_CONFIG": ("oci_config_path", Path),
    }
    for name, (key, converter) in settings.items():
        if os.environ.get(name):
            values[key] = converter(os.environ[name])
    values.update({key: value for key, value in overrides.items() if value is not None})
    return Config(**values)
