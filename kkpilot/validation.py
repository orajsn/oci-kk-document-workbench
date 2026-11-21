"""Bounded, deterministic validation; document text is always inert data."""

from __future__ import annotations

import binascii
import json
import re
import struct
from datetime import date


HOUSEHOLD_FIELDS = (
    "kk_number_raw", "form_serial", "head_name", "address", "rt", "rw", "village",
    "district", "regency_city", "province", "postal_code", "issued_date_raw",
)
MEMBER_FIELDS = (
    "row_number", "full_name", "nik_raw", "sex", "birth_place", "birth_date_raw", "religion",
    "education", "occupation", "blood_group", "marital_status", "marriage_date_raw",
    "relationship_to_head", "nationality", "passport_number", "residence_permit_number",
    "father_name", "mother_name", "combined_parent_name",
)
EVIDENCE_STATUSES = (
    "READABLE", "BLANK_ON_DOCUMENT", "UNREADABLE", "CROPPED", "NOT_PRESENT_IN_LAYOUT", "AMBIGUOUS", "NOT_REVIEWED",
)
MAX_MEMBERS = 100
MAX_EXTRACTION_BYTES = 2 * 1024 * 1024


class ValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class PayloadError(ValidationError):
    pass


def _shape(condition: bool, message: str) -> None:
    if not condition:
        raise PayloadError("INVALID_EXTRACTION", message)


def _text(value: object, *, nullable: bool = True, maximum: int = 2000) -> bool:
    if value is None:
        return nullable
    if not isinstance(value, str) or len(value) > maximum or "\x00" in value:
        return False
    try:
        value.encode("utf-8")
    except UnicodeError:
        return False
    return True


def assert_valid_payload(payload: object) -> None:
    """Reject invalid JSON shapes; do not silently coerce or invent extracted values."""
    _shape(isinstance(payload, dict), "Extraction must be a JSON object.")
    required = {"schema_version", "layout_family", "household", "members", "field_evidence", "review_flags"}
    _shape(set(payload) == required, "Extraction has missing or unsupported top-level fields.")
    _shape(payload["schema_version"] == "1.0", "Unsupported extraction schema version.")
    _shape(payload["layout_family"] in ("modern", "legacy", "unknown"), "Invalid layout family.")
    household = payload["household"]
    _shape(isinstance(household, dict) and set(household) == set(HOUSEHOLD_FIELDS), "Household fields must match the schema.")
    _shape(all(_text(value) for value in household.values()), "Household values must be bounded strings or null.")
    members = payload["members"]
    _shape(isinstance(members, list) and len(members) <= MAX_MEMBERS, "Members must be an array of at most 100 rows.")
    row_numbers: set[int] = set()
    paths = {"household." + name for name in HOUSEHOLD_FIELDS}
    for index, member in enumerate(members):
        _shape(isinstance(member, dict) and set(member) == set(MEMBER_FIELDS), "Member fields must match the schema.")
        number = member["row_number"]
        _shape(type(number) is int and 1 <= number <= 100, "Member row_number must be a positive integer up to 100.")
        _shape(number not in row_numbers, "Member row numbers must be unique.")
        row_numbers.add(number)
        _shape(all(_text(value) for name, value in member.items() if name != "row_number"), "Member values must be bounded strings or null.")
        for name in MEMBER_FIELDS:
            paths.add(f"members[{index}].{name}")
            paths.add(f"members.{index}.{name}")
    evidence = payload["field_evidence"]
    _shape(isinstance(evidence, list) and len(evidence) <= 5000, "Field evidence must be a bounded array.")
    for item in evidence:
        _shape(isinstance(item, dict) and {"path", "status", "section"} <= set(item) <= {"path", "status", "section", "row_number", "note"}, "Invalid field evidence entry.")
        _shape(_text(item["path"], nullable=False, maximum=200), "Invalid evidence path.")
        _shape(item["path"] in paths, "Evidence path does not identify an extracted field.")
        _shape(item["status"] in EVIDENCE_STATUSES, "Invalid evidence status.")
        _shape(_text(item["section"], nullable=False, maximum=200), "Invalid evidence section.")
        if "row_number" in item and item["row_number"] is not None:
            _shape(type(item["row_number"]) is int and 1 <= item["row_number"] <= 100, "Evidence row number must be a positive integer up to 100.")
        if "note" in item:
            _shape(_text(item["note"], nullable=False, maximum=500), "Invalid evidence note.")
    flags = payload["review_flags"]
    _shape(isinstance(flags, list) and len(flags) <= 500, "Review flags must be a bounded array.")
    for item in flags:
        _shape(isinstance(item, dict) and set(item) == {"path", "code", "message"}, "Invalid review flag entry.")
        _shape(all(_text(item[key], nullable=False, maximum=maximum) for key, maximum in (("path", 200), ("code", 80), ("message", 500))), "Invalid review flag text.")
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    _shape(len(encoded) <= MAX_EXTRACTION_BYTES, "Extraction exceeds the 2 MiB local record limit.")


def validate_payload(payload: object) -> list[dict]:
    """Return review issues without changing raw text or asserting OCR accuracy."""
    assert_valid_payload(payload)
    issues: list[dict] = []

    def issue(path: str, code: str, message: str, severity: str = "ERROR") -> None:
        issues.append({"path": path, "code": code, "message": message, "severity": severity})

    household = payload["household"]
    if payload["layout_family"] == "unknown":
        issue("layout_family", "LAYOUT_UNCLASSIFIED", "Classify the document as modern or legacy after checking the source image.")
    for field in ("kk_number_raw", "head_name"):
        if not household[field] or not household[field].strip():
            issue("household." + field, "MISSING_REQUIRED", "Review the required field against the source image.")
    if household["kk_number_raw"] and not re.fullmatch(r"[0-9]{16}", household["kk_number_raw"]):
        if payload["layout_family"] == "legacy":
            issue("household.kk_number_raw", "LEGACY_IDENTIFIER_REVIEW", "Check this legacy identifier against the source and record the review in a comment; preserve its original format.", "WARNING")
        else:
            issue("household.kk_number_raw", "IDENTIFIER_FORMAT", "Modern KK number must contain exactly 16 digits; do not infer missing digits.")
    if not payload["members"]:
        issue("members", "NO_MEMBERS", "At least one populated member row is required for acceptance.")
    seen_nik: set[str] = set()
    dates = [("household.issued_date_raw", household["issued_date_raw"])]
    for index, member in enumerate(payload["members"]):
        prefix = f"members[{index}]."
        if not any(value and value.strip() for name, value in member.items() if name != "row_number"):
            issue(prefix + "row_number", "BLANK_TEMPLATE_ROW", "Remove the blank template row.")
        for field in ("full_name", "nik_raw"):
            if not member[field] or not member[field].strip():
                issue(prefix + field, "MISSING_REQUIRED", "Review the required field against the source image.")
        nik = member["nik_raw"]
        if nik:
            if not re.fullmatch(r"[0-9]{16}", nik):
                if payload["layout_family"] == "legacy":
                    issue(prefix + "nik_raw", "LEGACY_IDENTIFIER_REVIEW", "Check this legacy identifier against the source and record the review in a comment; preserve its original format.", "WARNING")
                else:
                    issue(prefix + "nik_raw", "IDENTIFIER_FORMAT", "Modern NIK must contain exactly 16 digits; do not infer missing digits.")
            if nik in seen_nik:
                issue(prefix + "nik_raw", "DUPLICATE_NIK", "The same NIK occurs more than once in this document.")
            seen_nik.add(nik)
        if member["combined_parent_name"] and (member["father_name"] or member["mother_name"]):
            issue(prefix + "combined_parent_name", "PARENT_REVIEW", "Check separate parent values against the image; never split a legacy combined name by inference.", "WARNING")
        for field in ("birth_date_raw", "marriage_date_raw"):
            dates.append((prefix + field, member[field]))
    for path, raw in dates:
        if not raw:
            continue
        matched = re.fullmatch(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})", raw)
        iso = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", raw)
        try:
            if matched:
                day, month, year = map(int, matched.groups())
                date(year, month, day)
            elif iso:
                date(*map(int, iso.groups()))
            else:
                issue(path, "DATE_REVIEW", "Check the raw date against the source; its format is not recognized.", "WARNING")
        except ValueError:
            issue(path, "INVALID_DATE", "The raw value is not a valid calendar date.")
    expected_evidence = {"household." + field: (value, None) for field, value in household.items()}
    for index, member in enumerate(payload["members"]):
        for field, value in member.items():
            expected_evidence[f"members[{index}].{field}"] = (value, member["row_number"])
    seen_paths: set[str] = set()
    null_statuses = {"BLANK_ON_DOCUMENT", "UNREADABLE", "CROPPED", "NOT_PRESENT_IN_LAYOUT", "AMBIGUOUS"}
    for item in payload["field_evidence"]:
        path = re.sub(r"^members\.(\d+)\.", r"members[\1].", item["path"])
        if path in seen_paths:
            issue(path, "DUPLICATE_EVIDENCE", "Keep exactly one evidence entry for this field.")
        seen_paths.add(path)
        value, row_number = expected_evidence[path]
        if item.get("row_number") is not None and item["row_number"] != row_number:
            issue(path, "EVIDENCE_ROW_MISMATCH", "The evidence row number must identify the member selected by its field path.")
        if item["status"] == "READABLE" and (value is None or isinstance(value, str) and not value.strip()):
            issue(path, "EVIDENCE_VALUE_CONFLICT", "Readable evidence requires a populated value; check the image or change the evidence status.")
        if item["status"] in null_statuses and value is not None:
            issue(path, "EVIDENCE_VALUE_CONFLICT", "This evidence status requires a null value. Do not retain an inferred value for unavailable or ambiguous text.")
        if item["status"] == "NOT_REVIEWED":
            issue(path, "NOT_REVIEWED", "Review this field against the source before acceptance.")
        if item["status"] in ("UNREADABLE", "CROPPED", "AMBIGUOUS"):
            issue(path, item["status"], "The source evidence requires human review.", "WARNING")
    for path in expected_evidence:
        if path not in seen_paths:
            issue(path, "MISSING_EVIDENCE", "Record the evidence status for this field before acceptance.")
    for item in payload["review_flags"]:
        issue(item["path"], item["code"], item["message"], "WARNING")
    return issues


def inspect_image(data: bytes, max_pixels: int = 40_000_000) -> tuple[str, str, int, int]:
    """Check PNG/JPEG structure and bounded dimensions without decoding pixels.

    This rejects obvious truncation, extension spoofing and excessive dimensions.
    It is deliberately not a full image decoder or malware scanner.
    """
    if not isinstance(data, bytes):
        raise ValidationError("INVALID_IMAGE", "Image content must be bytes.")
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        width, height = _inspect_png(data)
        result = ("image/png", ".png", width, height)
    elif data.startswith(b"\xff\xd8"):
        width, height = _inspect_jpeg(data)
        result = ("image/jpeg", ".jpg", width, height)
    else:
        raise ValidationError("UNSUPPORTED_IMAGE", "Only PNG and JPEG images are supported.")
    if width <= 0 or height <= 0 or width * height > max_pixels:
        raise ValidationError("IMAGE_DIMENSIONS", "Image dimensions exceed the configured limit.")
    return result


def _inspect_png(data: bytes) -> tuple[int, int]:
    offset, width, height = 8, 0, 0
    seen_header = seen_data = seen_end = False
    while offset + 12 <= len(data):
        size = struct.unpack_from(">I", data, offset)[0]
        kind = data[offset + 4:offset + 8]
        end = offset + size + 12
        if end > len(data):
            break
        body = data[offset + 8:offset + 8 + size]
        crc = struct.unpack_from(">I", data, offset + 8 + size)[0]
        if binascii.crc32(kind + body) & 0xFFFFFFFF != crc:
            raise ValidationError("INVALID_IMAGE", "PNG integrity check failed.")
        if not seen_header:
            if kind != b"IHDR" or size != 13:
                raise ValidationError("INVALID_IMAGE", "PNG header is invalid.")
            width, height, depth, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", body)
            valid_depths = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
            if depth not in valid_depths.get(color, ()) or compression or filtering or interlace not in (0, 1):
                raise ValidationError("INVALID_IMAGE", "PNG image format is invalid.")
            seen_header = True
        elif kind == b"IHDR":
            raise ValidationError("INVALID_IMAGE", "PNG contains duplicate headers.")
        if kind == b"IDAT" and size:
            seen_data = True
        if kind == b"IEND":
            seen_end = size == 0 and end == len(data)
            break
        offset = end
    if not (seen_header and seen_data and seen_end):
        raise ValidationError("INVALID_IMAGE", "PNG image is incomplete.")
    return width, height


def _inspect_jpeg(data: bytes) -> tuple[int, int]:
    offset, width, height = 2, 0, 0
    seen_scan = False
    sof_markers = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
    while offset < len(data):
        if data[offset] != 0xFF:
            raise ValidationError("INVALID_IMAGE", "JPEG marker structure is invalid.")
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            break
        marker = data[offset]
        offset += 1
        if marker == 0xD9:
            trailer = data[offset:]
            if width and height and seen_scan and len(trailer) <= 16 and all(byte in b"\r\n \t" for byte in trailer):
                return width, height
            break
        if marker in (0, 0xD8) or 0xD0 <= marker <= 0xD7:
            raise ValidationError("INVALID_IMAGE", "JPEG marker structure is invalid.")
        if marker == 1:
            continue
        if offset + 2 > len(data):
            break
        length = struct.unpack_from(">H", data, offset)[0]
        if length < 2 or offset + length > len(data):
            break
        if marker in sof_markers:
            if length < 8:
                break
            height, width = struct.unpack_from(">HH", data, offset + 3)
            components = data[offset + 7]
            if components not in (1, 3, 4) or length != 8 + 3 * components:
                break
        offset += length
        if marker == 0xDA:
            seen_scan = True
            # Skip byte-stuffed entropy data; resume at the next real marker.
            while offset < len(data):
                if data[offset] != 0xFF:
                    offset += 1
                    continue
                if offset + 1 >= len(data):
                    break
                following = data[offset + 1]
                if following == 0 or 0xD0 <= following <= 0xD7:
                    offset += 2
                    continue
                if following == 0xFF:
                    offset += 1
                    continue
                break
    raise ValidationError("INVALID_IMAGE", "JPEG image is incomplete or invalid.")
