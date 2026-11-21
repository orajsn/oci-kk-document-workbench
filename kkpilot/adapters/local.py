"""Local demonstration/replay. Neither adapter performs AI/OCR inference."""
import copy
import hashlib
import json
from pathlib import Path
from .errors import AdapterError

ROOT = Path(__file__).resolve().parents[2]

def empty_payload():
    contract = json.loads((ROOT / "docs/implementation-contract.json").read_text(encoding="utf-8"))
    return {"schema_version": "1.0", "layout_family": "unknown",
            "household": {k: None for k in contract["household_fields"]},
            "members": [], "field_evidence": [], "review_flags": []}

class DemoExtractor:
    """Synthetic values independent of the uploaded image; never an accuracy test."""
    def extract(self, image_path: Path, context: dict) -> dict:
        payload = empty_payload()
        contract = json.loads((ROOT / "docs/implementation-contract.json").read_text(encoding="utf-8"))
        payload["layout_family"] = "legacy"
        payload["household"].update(kk_number_raw="DEMO-HOUSEHOLD", head_name="SYNTHETIC PERSON A",
                                    address="FICTIONAL DEMONSTRATION ADDRESS", province="DEMO")
        for row, name, relationship in [(1, "SYNTHETIC PERSON A", "KEPALA KELUARGA"),
                                         (2, "SYNTHETIC PERSON B", "ANAK")]:
            member = {k: None for k in contract["member_fields"]}
            member.update(row_number=row, full_name=name, nik_raw=f"DEMO-MEMBER-{row}",
                          relationship_to_head=relationship)
            payload["members"].append(member)
        for key, value in payload["household"].items():
            payload["field_evidence"].append({"path": "household." + key,
                "status": "READABLE" if value is not None else "NOT_PRESENT_IN_LAYOUT",
                "section": "Synthetic Demonstration"})
        for i, member in enumerate(payload["members"]):
            for key, value in member.items():
                payload["field_evidence"].append({"path": f"members[{i}].{key}",
                    "status": "READABLE" if value is not None else "NOT_PRESENT_IN_LAYOUT",
                    "section": "Synthetic Demonstration", "row_number": member["row_number"]})
        payload["review_flags"] = [{"path": "$", "code": "SYNTHETIC_DEMO",
                                     "message": "Synthetic demonstration only. No uploaded text was read."}]
        return payload

class ReferenceExtractor:
    """Replay a separately inspected draft by exact hash. Never a model prediction."""
    def __init__(self, reference_dir):
        if not reference_dir:
            raise AdapterError("REFERENCE_DIRECTORY_REQUIRED")
        self.reference_dir = Path(reference_dir).expanduser().resolve()
        if not self.reference_dir.is_dir():
            raise AdapterError("REFERENCE_DIRECTORY_NOT_FOUND")

    def extract(self, image_path: Path, context: dict) -> dict:
        digest = hashlib.sha256(Path(image_path).read_bytes()).hexdigest()
        if context.get("sha256") and context["sha256"] != digest:
            raise AdapterError("SOURCE_HASH_MISMATCH")
        found = []
        for p in sorted(self.reference_dir.glob("KK-*.json")):
            # Do not follow a reference file symlink outside the explicitly allowed directory.
            if not p.resolve().is_relative_to(self.reference_dir) or p.stat().st_size > 5_000_000:
                continue
            try:
                value = json.loads(p.read_text(encoding="utf-8-sig"))
            except (ValueError, OSError):
                continue
            if value.get("source_sha256") == digest and isinstance(value.get("extraction"), dict):
                found.append(value)
        if not found:
            raise AdapterError("REFERENCE_NOT_FOUND")
        if len(found) != 1:
            raise AdapterError("REFERENCE_HASH_AMBIGUOUS")
        payload = copy.deepcopy(found[0]["extraction"])
        payload.setdefault("review_flags", []).append({"path": "$", "code": "REFERENCE_REPLAY",
            "message": "Replayed provisional visual reference, not an AI prediction; KK verification required."})
        return payload
