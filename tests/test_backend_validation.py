"""Contract and image boundary tests using synthetic in-memory records only."""

from copy import deepcopy
import base64
import json
import struct
import unittest
import zlib

from kkpilot.validation import (
    PayloadError, ValidationError, assert_valid_payload, inspect_image,
    validate_payload,
)


HOUSEHOLD_FIELDS = (
    "kk_number_raw", "form_serial", "head_name", "address", "rt", "rw",
    "village", "district", "regency_city", "province", "postal_code",
    "issued_date_raw",
)
MEMBER_FIELDS = (
    "row_number", "full_name", "nik_raw", "sex", "birth_place",
    "birth_date_raw", "religion", "education", "occupation", "blood_group",
    "marital_status", "marriage_date_raw", "relationship_to_head",
    "nationality", "passport_number", "residence_permit_number", "father_name",
    "mother_name", "combined_parent_name",
)


def payload():
    household = dict.fromkeys(HOUSEHOLD_FIELDS)
    household.update(kk_number_raw="0000000000000000", head_name="SYNTHETIC TEST")
    member = dict.fromkeys(MEMBER_FIELDS)
    member.update(row_number=1, full_name="SYNTHETIC TEST", nik_raw="1111111111111111")
    evidence = [
        {"path": f"household.{field}", "status": "READABLE" if value is not None and value != "" else "BLANK_ON_DOCUMENT",
         "section": "household"}
        for field, value in household.items()
    ]
    evidence.extend(
        {"path": f"members[0].{field}", "status": "READABLE" if value is not None and value != "" else "BLANK_ON_DOCUMENT",
         "section": "members", "row_number": 1}
        for field, value in member.items()
    )
    return {
        "schema_version": "1.0", "layout_family": "modern",
        "household": household, "members": [member],
        "field_evidence": evidence, "review_flags": [],
    }


def png(width=1, height=1):
    def chunk(kind, content):
        return (struct.pack(">I", len(content)) + kind + content
                + struct.pack(">I", zlib.crc32(kind + content)))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress((b"\0" + b"\0\0\0" * width) * height))
            + chunk(b"IEND", b""))


# Synthetic 2x3 black JPEG generated once with Windows System.Drawing. No image
# library or operating-system dependency is needed to run this fixture.
JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAMCAgMCAgMDAwMEAwMEBQgFBQQEBQoHBwYIDAoMDAsKCwsNDhIQDQ4RDgsLEBYQERMUFRUVDA8XGBYUGBIUFRT/2wBDAQMEBAUEBQkFBQkUDQsNFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBT/wAARCAADAAIDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwD8qqKKKAP/2Q=="
)


class PayloadValidationTests(unittest.TestCase):
    def test_full_contract_shape_is_accepted_without_mutation(self):
        candidate = payload()
        before = deepcopy(candidate)
        assert_valid_payload(candidate)
        issues = validate_payload(candidate)
        self.assertIsInstance(issues, list)
        self.assertEqual([], issues)
        self.assertEqual(before, candidate)

    def test_missing_any_field_evidence_is_blocking_without_changing_draft(self):
        for path in ("household.address", "members[0].nik_raw", "members[0].row_number"):
            candidate = payload()
            candidate["field_evidence"] = [entry for entry in candidate["field_evidence"] if entry["path"] != path]
            before = deepcopy(candidate)
            with self.subTest(path=path):
                assert_valid_payload(candidate)
                self.assertTrue(any(issue["severity"] == "ERROR" and issue["path"] == path
                                    for issue in validate_payload(candidate)))
                self.assertEqual(before, candidate)

    def test_removing_all_evidence_cannot_satisfy_acceptance_validation(self):
        candidate = payload()
        candidate["field_evidence"] = []
        assert_valid_payload(candidate)
        errors = [issue for issue in validate_payload(candidate) if issue["severity"] == "ERROR"]
        self.assertTrue(errors)

    def test_member_dot_alias_covers_field_but_duplicate_alias_is_blocking(self):
        candidate = payload()
        entry = next(entry for entry in candidate["field_evidence"] if entry["path"] == "members[0].nik_raw")
        entry["path"] = "members.0.nik_raw"
        self.assertFalse(any(issue["severity"] == "ERROR" for issue in validate_payload(candidate)))
        duplicate = deepcopy(entry)
        duplicate["path"] = "members[0].nik_raw"
        candidate["field_evidence"].append(duplicate)
        assert_valid_payload(candidate)
        self.assertTrue(any(issue["severity"] == "ERROR" for issue in validate_payload(candidate)))

    def test_evidence_row_must_match_path_even_if_other_row_exists(self):
        candidate = payload()
        second = deepcopy(candidate["members"][0])
        second.update(row_number=2, nik_raw="2222222222222222")
        candidate["members"].append(second)
        for field, value in second.items():
            candidate["field_evidence"].append({
                "path": f"members[1].{field}", "status": "READABLE" if value is not None else "BLANK_ON_DOCUMENT",
                "section": "members", "row_number": 2,
            })
        self.assertFalse(any(issue["severity"] == "ERROR" for issue in validate_payload(candidate)))
        entry = next(entry for entry in candidate["field_evidence"] if entry["path"] == "members[0].nik_raw")
        entry["row_number"] = 2
        assert_valid_payload(candidate)
        self.assertTrue(any(issue["severity"] == "ERROR" for issue in validate_payload(candidate)))

    def test_readable_evidence_requires_nonblank_value(self):
        for value in (None, "", " \t "):
            candidate = payload()
            candidate["household"]["address"] = value
            entry = next(entry for entry in candidate["field_evidence"] if entry["path"] == "household.address")
            entry["status"] = "READABLE"
            with self.subTest(value=value):
                assert_valid_payload(candidate)
                self.assertTrue(any(issue["severity"] == "ERROR" and issue["path"] == "household.address"
                                    for issue in validate_payload(candidate)))

    def test_nonreadable_evidence_requires_null_instead_of_invented_value(self):
        for status in ("BLANK_ON_DOCUMENT", "UNREADABLE", "CROPPED", "NOT_PRESENT_IN_LAYOUT", "AMBIGUOUS"):
            for value in ("Synthetic invented address", ""):
                candidate = payload()
                candidate["household"]["address"] = value
                entry = next(entry for entry in candidate["field_evidence"] if entry["path"] == "household.address")
                entry["status"] = status
                with self.subTest(status=status, value=value):
                    assert_valid_payload(candidate)
                    self.assertTrue(any(issue["severity"] == "ERROR" and issue["path"] == "household.address"
                                        for issue in validate_payload(candidate)))

    def test_unknown_layout_remains_a_valid_draft_but_blocks_acceptance(self):
        candidate = payload()
        candidate["layout_family"] = "unknown"
        assert_valid_payload(candidate)
        self.assertTrue(any(issue["severity"] == "ERROR" and issue["path"] == "layout_family"
                            for issue in validate_payload(candidate)))

    def test_model_text_and_nonobjects_are_not_accepted_as_payload(self):
        for candidate in ("{not valid json}", "```json\n{}\n```", [], None, 42):
            with self.subTest(candidate=repr(candidate)), self.assertRaises(PayloadError):
                assert_valid_payload(candidate)

    def test_large_valid_field_collection_is_rejected_by_total_payload_bound(self):
        candidate = payload()
        candidate["members"] = []
        for row in range(1, 101):
            member = dict.fromkeys(MEMBER_FIELDS, "S" * 1800)
            member["row_number"] = row
            member["nik_raw"] = f"{row:016d}"
            candidate["members"].append(member)
        self.assertGreater(len(json.dumps(candidate, ensure_ascii=False).encode("utf-8")), 2 * 1024 * 1024)
        with self.assertRaises(PayloadError):
            assert_valid_payload(candidate)

    def test_total_payload_bound_counts_utf8_bytes_not_only_characters(self):
        candidate = payload()
        candidate["members"] = []
        for row in range(1, 101):
            member = dict.fromkeys(MEMBER_FIELDS, "測" * 500)
            member["row_number"] = row
            member["nik_raw"] = f"{row:016d}"
            candidate["members"].append(member)
        encoded = json.dumps(candidate, ensure_ascii=False, separators=(",", ":"))
        self.assertLess(len(encoded), 2 * 1024 * 1024)
        self.assertGreater(len(encoded.encode("utf-8")), 2 * 1024 * 1024)
        with self.assertRaises(PayloadError):
            assert_valid_payload(candidate)

    def test_missing_required_top_level_key_rejected(self):
        for key in payload():
            candidate = payload()
            del candidate[key]
            with self.subTest(key=key), self.assertRaises(PayloadError):
                assert_valid_payload(candidate)

    def test_schema_version_and_layout_family_must_be_known(self):
        for field, value in (("schema_version", "99"), ("layout_family", "guessed")):
            candidate = payload()
            candidate[field] = value
            with self.subTest(field=field), self.assertRaises(PayloadError):
                assert_valid_payload(candidate)

    def test_household_raw_identifiers_must_be_strings_not_numbers(self):
        for value in (1234567890123456, True, {"raw": "123"}, ["123"]):
            candidate = payload()
            candidate["household"]["kk_number_raw"] = value
            with self.subTest(value=value), self.assertRaises(PayloadError):
                assert_valid_payload(candidate)

    def test_positive_integer_member_rows_exclude_boolean(self):
        for value in (0, -1, 1.0, "1", True, None):
            candidate = payload()
            candidate["members"][0]["row_number"] = value
            with self.subTest(value=value), self.assertRaises(PayloadError):
                assert_valid_payload(candidate)

    def test_evidence_status_must_match_contract(self):
        candidate = payload()
        candidate["field_evidence"] = [{
            "path": "household.kk_number_raw", "status": "GUESSED",
            "section": "household",
        }]
        with self.assertRaises(PayloadError):
            assert_valid_payload(candidate)

    def test_unknown_nested_fields_are_rejected(self):
        candidate = payload()
        candidate["members"][0]["run_command"] = "arbitrary model instruction"
        with self.assertRaises(PayloadError):
            assert_valid_payload(candidate)

    def test_all_known_household_and_member_fields_are_required(self):
        for section, field in (("household", "address"), ("members", "father_name")):
            candidate = payload()
            target = candidate[section] if section == "household" else candidate[section][0]
            del target[field]
            with self.subTest(section=section, field=field), self.assertRaises(PayloadError):
                assert_valid_payload(candidate)

    def test_duplicate_row_numbers_are_rejected(self):
        candidate = payload()
        candidate["members"].append(deepcopy(candidate["members"][0]))
        with self.assertRaises(PayloadError):
            assert_valid_payload(candidate)

    def test_duplicate_nik_with_distinct_rows_is_visible_to_reviewer(self):
        candidate = payload()
        duplicate = deepcopy(candidate["members"][0])
        duplicate["row_number"] = 2
        candidate["members"].append(duplicate)
        assert_valid_payload(candidate)
        issues = validate_payload(candidate)
        self.assertTrue(any("nik_raw" in issue["path"] for issue in issues))

    def test_invalid_calendar_date_is_reported_without_normalizing_raw_value(self):
        candidate = payload()
        candidate["members"][0]["birth_date_raw"] = "31-02-2000"
        issues = validate_payload(candidate)
        self.assertTrue(any("birth_date_raw" in issue["path"] for issue in issues))
        self.assertEqual("31-02-2000", candidate["members"][0]["birth_date_raw"])

    def test_short_or_unknown_digits_are_flagged_and_never_repaired(self):
        candidate = payload()
        candidate["household"]["kk_number_raw"] = "1234?"
        candidate["members"][0]["nik_raw"] = "0000123"
        original = deepcopy(candidate)
        assert_valid_payload(candidate)
        issues = validate_payload(candidate)
        self.assertTrue(any("kk_number_raw" in issue["path"] for issue in issues))
        self.assertTrue(any("nik_raw" in issue["path"] for issue in issues))
        self.assertEqual(original, candidate)

    def test_legacy_identifier_formats_require_review_and_preserve_exact_raw_text(self):
        for kk_raw, nik_raw in (
            ("00.00/0001", "11.11.0002"),
            ("0000001", "000002"),
            ("00/00/0001", "0011/0022/0033"),
        ):
            candidate = payload()
            candidate["layout_family"] = "legacy"
            candidate["household"]["kk_number_raw"] = kk_raw
            candidate["members"][0]["nik_raw"] = nik_raw
            before = deepcopy(candidate)
            with self.subTest(kk_raw=kk_raw, nik_raw=nik_raw):
                assert_valid_payload(candidate)
                issues = validate_payload(candidate)
                for path in ("household.kk_number_raw", "members[0].nik_raw"):
                    identifier_issues = [issue for issue in issues if issue["path"] == path]
                    self.assertTrue(any(
                        issue["code"] == "LEGACY_IDENTIFIER_REVIEW" and issue["severity"] == "WARNING"
                        for issue in identifier_issues
                    ))
                    self.assertFalse(any(issue["severity"] == "ERROR" for issue in identifier_issues))
                self.assertEqual(before, candidate)

    def test_legacy_format_exception_does_not_apply_to_modern_or_unknown_layout(self):
        for layout in ("modern", "unknown"):
            candidate = payload()
            candidate["layout_family"] = layout
            candidate["household"]["kk_number_raw"] = "00.00/0001"
            candidate["members"][0]["nik_raw"] = "000002"
            with self.subTest(layout=layout):
                issues = validate_payload(candidate)
                for path in ("household.kk_number_raw", "members[0].nik_raw"):
                    self.assertTrue(any(
                        issue["path"] == path and issue["code"] == "IDENTIFIER_FORMAT"
                        and issue["severity"] == "ERROR" for issue in issues
                    ))

    def test_legacy_layout_still_requires_identifiers_to_be_present(self):
        for raw in (None, "", "   "):
            candidate = payload()
            candidate["layout_family"] = "legacy"
            candidate["household"]["kk_number_raw"] = raw
            candidate["members"][0]["nik_raw"] = raw
            with self.subTest(raw=raw):
                issues = validate_payload(candidate)
                for path in ("household.kk_number_raw", "members[0].nik_raw"):
                    self.assertTrue(any(
                        issue["path"] == path and issue["code"] == "MISSING_REQUIRED"
                        and issue["severity"] == "ERROR" for issue in issues
                    ))

    def test_legacy_combined_parent_value_is_preserved_unsplit(self):
        candidate = payload()
        candidate["layout_family"] = "legacy"
        candidate["members"][0]["combined_parent_name"] = "PARENT A / PARENT B"
        before = deepcopy(candidate)
        assert_valid_payload(candidate)
        validate_payload(candidate)
        self.assertEqual(before, candidate)
        self.assertIsNone(candidate["members"][0]["father_name"])
        self.assertIsNone(candidate["members"][0]["mother_name"])

    def test_unreadable_evidence_remains_visible_to_review(self):
        candidate = payload()
        candidate["members"][0]["nik_raw"] = None
        candidate["field_evidence"] = [{
            "path": "members[0].nik_raw", "status": "UNREADABLE",
            "section": "members", "row_number": 1, "note": "blurred",
        }]
        assert_valid_payload(candidate)
        issues = validate_payload(candidate)
        self.assertTrue(any("nik_raw" in issue["path"] for issue in issues))

    def test_not_reviewed_evidence_is_a_blocking_issue(self):
        candidate = payload()
        candidate["field_evidence"] = [{
            "path": "household.address", "status": "NOT_REVIEWED", "section": "household",
        }]
        assert_valid_payload(candidate)
        issues = validate_payload(candidate)
        self.assertTrue(any(
            issue["path"] == "household.address" and issue["severity"] == "ERROR"
            for issue in issues
        ))

    def test_blank_template_row_is_flagged_without_inventing_member_values(self):
        candidate = payload()
        member = dict.fromkeys(MEMBER_FIELDS)
        member["row_number"] = 2
        candidate["members"].append(member)
        before = deepcopy(candidate)
        issues = validate_payload(candidate)
        self.assertTrue(any(issue["code"] == "BLANK_TEMPLATE_ROW" for issue in issues))
        self.assertEqual(before, candidate)

    def test_suspicious_text_is_data_and_does_not_change_validation_structure(self):
        candidate = payload()
        marker = "<img src=x onerror=alert(1)>; DROP TABLE jobs; --"
        candidate["household"]["address"] = marker
        assert_valid_payload(candidate)
        issues = validate_payload(candidate)
        self.assertIsInstance(issues, list)
        self.assertEqual(marker, candidate["household"]["address"])
        for issue in issues:
            self.assertTrue({"path", "code", "message", "severity"}.issubset(issue))
            self.assertIn(issue["severity"], {"ERROR", "WARNING"})


class ImageInspectionTests(unittest.TestCase):
    def test_valid_png_detected_from_content(self):
        mime, extension, width, height = inspect_image(png(2, 3))
        self.assertEqual("image/png", mime)
        self.assertEqual("png", extension.lstrip("."))
        self.assertEqual((2, 3), (width, height))

    def test_valid_jpeg_detected_from_content(self):
        mime, extension, width, height = inspect_image(JPEG)
        self.assertEqual("image/jpeg", mime)
        self.assertIn(extension.lstrip("."), {"jpg", "jpeg"})
        self.assertEqual((2, 3), (width, height))

    def test_jpeg_missing_end_marker_is_rejected(self):
        for source in (JPEG[:-2], JPEG[:-2] + b"\r\n"):
            with self.subTest(trailing_whitespace=source.endswith(b"\r\n")), self.assertRaises(ValidationError):
                inspect_image(source)

    def test_jpeg_allows_only_small_whitespace_trailer_without_rewriting_source(self):
        for trailer in (b"\r\n", b" \t\r\n", b"\n" * 16):
            with self.subTest(trailer=trailer):
                source = JPEG + trailer
                original = bytes(source)
                self.assertEqual(inspect_image(JPEG), inspect_image(source))
                self.assertEqual(original, source)

    def test_jpeg_rejects_nonwhitespace_or_excessive_trailer(self):
        for trailer in (b"\x00", b"\r\n\x00", b"<script>x</script>", b"\n" * 17, b"\v", b"\f"):
            with self.subTest(trailer=trailer), self.assertRaises(ValidationError):
                inspect_image(JPEG + trailer)

    def test_corrupted_png_crc_is_rejected(self):
        data = bytearray(png())
        data[29] ^= 1
        with self.assertRaises(ValidationError):
            inspect_image(bytes(data))

    def test_html_svg_and_arbitrary_bytes_cannot_spoof_image_upload(self):
        for value in (b"<html>not image</html>", b"<svg></svg>", b"", b"random"):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                inspect_image(value)

    def test_signature_only_and_truncated_png_are_rejected(self):
        original = png()
        for value in (original[:8], original[:24], original[:-12]):
            with self.subTest(length=len(value)), self.assertRaises(ValidationError):
                inspect_image(value)

    def test_zero_dimensions_are_rejected(self):
        with self.assertRaises(ValidationError):
            inspect_image(png(0, 1))

    def test_pixel_limit_applies_before_image_decoding(self):
        with self.assertRaises(ValidationError):
            inspect_image(png(20, 20), max_pixels=100)

    def test_jpeg_magic_without_frame_is_rejected(self):
        for value in (b"\xff\xd8\xff\xd9", b"\xff\xd8\xff\xe0" + b"X" * 50):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                inspect_image(value)


if __name__ == "__main__":
    unittest.main()
