"""Real loopback HTTP checks. All fixture content is synthetic; no external calls."""

import base64
import http.client
import json
import os
import socket
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from kkpilot.config import Config, from_environment
from kkpilot.server import PilotServer
from test_backend_validation import payload, png


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = Config(data_dir=Path(self.temp.name), port=0, max_upload_bytes=1024)
        self.server = PilotServer(self.config, start_workers=False)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]
        self.token = self.server.csrf_token
        self.addCleanup(self.cleanup_server)

    def cleanup_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.temp.cleanup()

    def request(self, method, path, value=None, *, headers=None, raw=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        supplied = {"Content-Type": "application/json", "X-KK-Pilot-Token": self.token}
        supplied.update(headers or {})
        body = raw if raw is not None else json.dumps(value).encode() if value is not None else None
        connection.request(method, path, body=body, headers=supplied)
        response = connection.getresponse()
        content = response.read()
        result = (response.status, dict(response.getheaders()), content)
        connection.close()
        return result

    def json_request(self, method, path, value=None, **kwargs):
        status, headers, raw = self.request(method, path, value, **kwargs)
        return status, headers, json.loads(raw)

    def upload_payload(self, **overrides):
        value = {"filename": "synthetic.png", "case_id": "synthetic-case", "idempotency_key": "same-key", "mode": "demo", "content_base64": base64.b64encode(png()).decode()}
        value.update(overrides)
        return value

    def upload(self, **overrides):
        status, _, result = self.json_request("POST", "/api/jobs", self.upload_payload(**overrides))
        self.assertEqual(201, status, result)
        return result

    def test_health_config_are_honest_and_do_not_expose_private_paths(self):
        status, headers, config = self.json_request("GET", "/api/config")
        self.assertEqual(200, status)
        self.assertEqual(self.token, config["csrf_token"])
        self.assertTrue(config["local_only"])
        self.assertFalse(config["live_oci_enabled"])
        self.assertEqual([True, False, False], [mode["available"] for mode in config["modes"]])
        self.assertNotIn(self.temp.name, json.dumps(config))
        self.assertEqual("no-store", headers["Cache-Control"])
        status, _, health = self.json_request("GET", "/api/health")
        self.assertEqual(200, status)
        self.assertNotIn("csrf_token", health)
        self.assertFalse(health["live_oci_enabled"])

    def test_upload_process_review_export_end_to_end(self):
        uploaded = self.upload()
        job_id = uploaded["job"]["id"]
        self.assertEqual("QUEUED", uploaded["job"]["status"])
        with patch("socket.create_connection", side_effect=AssertionError("No external connection allowed")):
            self.assertTrue(self.server.workers.process_once())
        status, _, detail = self.json_request("GET", f"/api/jobs/{job_id}")
        self.assertEqual(200, status)
        self.assertEqual("NEEDS_REVIEW", detail["job"]["status"])
        self.assertIn("not AI", detail["job"]["processing_label"])
        reviewed = {"expected_version": detail["job"]["version"], "decision": "ACCEPTED", "extraction": payload(), "comment": "Synthetic review test"}
        status, _, accepted = self.json_request("POST", f"/api/jobs/{job_id}/review", reviewed)
        self.assertEqual(200, status, accepted)
        self.assertEqual("ACCEPTED", accepted["job"]["status"])
        self.assertEqual(1, len(accepted["review_history"]))
        status, headers, exported = self.json_request("GET", f"/api/jobs/{job_id}/export")
        self.assertEqual(200, status)
        self.assertTrue(exported["export"]["human_accepted"])
        self.assertFalse(exported["export"]["is_ai_extraction"])
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertIn("previous_extraction", exported["review_history"][0])
        self.assertNotIn(self.temp.name, json.dumps(exported))

    def test_arbitrary_display_filename_cannot_escape_image_directory(self):
        detail = self.upload(filename="../../outside.html<script>alert(1)</script>")
        images = list(self.config.image_dir.iterdir())
        self.assertEqual(1, len(images))
        self.assertRegex(images[0].name, r"^[0-9a-f]{32}\.png$")
        self.assertEqual("../../outside.html<script>alert(1)</script>", detail["job"]["filename"])
        status, headers, image = self.request("GET", f"/api/jobs/{detail['job']['id']}/image")
        self.assertEqual(200, status)
        self.assertEqual(png(), image)
        self.assertEqual("image/png", headers["Content-Type"])

    def test_http_idempotency_replay_and_conflict_remove_orphan_uploads(self):
        first = self.upload()
        status, _, replay = self.json_request("POST", "/api/jobs", self.upload_payload())
        self.assertEqual(200, status)
        self.assertEqual(first["job"]["id"], replay["job"]["id"])
        status, _, conflict = self.json_request("POST", "/api/jobs", self.upload_payload(filename="changed.png"))
        self.assertEqual(409, status)
        self.assertEqual("IDEMPOTENCY_CONFLICT", conflict["error"]["code"])
        self.assertEqual(1, len(list(self.config.image_dir.iterdir())))

    def test_concurrent_duplicate_uploads_leave_one_source_and_one_job(self):
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: self.json_request("POST", "/api/jobs", self.upload_payload()), range(6)))
        self.assertEqual([200] * 5 + [201], sorted(item[0] for item in results))
        self.assertEqual(1, len({item[2]["job"]["id"] for item in results}))
        self.assertEqual(1, len(list(self.config.image_dir.iterdir())))

    def test_mutation_requires_session_token_and_exact_same_origin(self):
        for headers in ({"X-KK-Pilot-Token": ""}, {"X-KK-Pilot-Token": "é"}, {"Origin": "https://evil.example"}, {"Origin": "null"}, {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-origin"}):
            with self.subTest(headers=headers):
                status, _, _ = self.json_request("POST", "/api/jobs", self.upload_payload(), headers=headers)
                self.assertEqual(403, status)
        status, _, _ = self.json_request("POST", "/api/jobs", self.upload_payload(), headers={"Origin": f"http://127.0.0.1:{self.port}", "Sec-Fetch-Site": "same-origin"})
        self.assertEqual(201, status)

    def test_host_rebinding_and_cross_site_image_reads_are_denied(self):
        detail = self.upload()
        for path in ("/api/config", f"/api/jobs/{detail['job']['id']}/image"):
            for headers in ({"Host": "evil.example"}, {"Host": f"127.0.0.1.evil.example:{self.port}"}, {"Sec-Fetch-Site": "cross-site"}, {"Origin": "https://evil.example"}):
                with self.subTest(path=path, headers=headers):
                    status, _, _ = self.json_request("GET", path, headers=headers)
                    self.assertEqual(403, status)

    def test_external_top_level_navigation_can_open_public_ui_but_not_api(self):
        headers = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}
        status, _, raw = self.request("GET", "/", headers=headers)
        self.assertEqual(200, status, raw)
        self.assertIn(b"<!doctype html", raw.lower())
        self.assertEqual(403, self.json_request("GET", "/api/config", headers=headers)[0])
        self.assertEqual(403, self.json_request("GET", "/api/jobs", headers=headers)[0])
        self.assertEqual(403, self.json_request("GET", "/", headers={**headers, "Origin": "https://evil.example"})[0])

    def test_malformed_duplicate_and_nonfinite_json_rejected(self):
        for raw in (b"{broken}", b'{"mode":"demo","mode":"oci"}', b'{"x":NaN}', b"[]", b"null", b"{" * 2000):
            with self.subTest(raw=raw[:30]):
                status, _, _ = self.json_request("POST", "/api/jobs", raw=raw)
                self.assertEqual(400, status)
        self.assertEqual([], self.server.repository.list_jobs())

    def test_oversize_unsupported_spoofed_and_invalid_images_rejected(self):
        for content in (b"<html>not an image</html>", b"\xff\xd8\xff\xd9", b"\x89PNG\r\n\x1a\n"):
            status, _, _ = self.json_request("POST", "/api/jobs", self.upload_payload(content_base64=base64.b64encode(content).decode()))
            self.assertEqual(400, status)
        status, _, _ = self.json_request("POST", "/api/jobs", self.upload_payload(content_base64="%%%invalid%%%"))
        self.assertEqual(400, status)
        status, _, _ = self.json_request("POST", "/api/jobs", self.upload_payload(content_base64=base64.b64encode(b"x" * 1100).decode()))
        self.assertEqual(413, status)
        self.assertEqual([], list(self.config.image_dir.iterdir()))

    def test_disabled_live_mode_and_unconfigured_reference_are_not_silently_substituted(self):
        for mode, expected in (("oci", 400), ("reference", 409), (None, 400)):
            status, _, _ = self.json_request("POST", "/api/jobs", self.upload_payload(mode=mode))
            self.assertEqual(expected, status)
        self.assertEqual([], self.server.repository.list_jobs())

    def test_source_integrity_is_checked_before_serving(self):
        detail = self.upload()
        next(self.config.image_dir.iterdir()).write_bytes(b"tampered")
        status, _, result = self.json_request("GET", f"/api/jobs/{detail['job']['id']}/image")
        self.assertEqual(409, status)
        self.assertEqual("SOURCE_CHANGED", result["error"]["code"])

    def test_stale_review_returns_conflict_and_keeps_winning_audit(self):
        detail = self.upload()
        self.server.workers.process_once()
        job_id = detail["job"]["id"]
        detail = self.server.repository.get_job(job_id)
        review = {"expected_version": detail["job"]["version"], "decision": "NEEDS_REVIEW", "extraction": payload(), "comment": "test"}
        self.assertEqual(200, self.json_request("POST", f"/api/jobs/{job_id}/review", review)[0])
        status, _, result = self.json_request("POST", f"/api/jobs/{job_id}/review", review)
        self.assertEqual(409, status)
        self.assertEqual("VERSION_CONFLICT", result["error"]["code"])
        self.assertEqual(1, len(self.server.repository.get_job(job_id)["review_history"]))

    def test_invalid_unicode_in_review_is_rejected_without_server_error(self):
        detail = self.upload()
        self.server.workers.process_once()
        job_id = detail["job"]["id"]
        detail = self.server.repository.get_job(job_id)
        review = {"expected_version": detail["job"]["version"], "decision": "NEEDS_REVIEW", "extraction": payload(), "comment": "\ud800"}
        self.assertEqual(400, self.json_request("POST", f"/api/jobs/{job_id}/review", review)[0])
        review["comment"] = "Valid comment"
        review["extraction"]["household"]["head_name"] = "\ud800"
        self.assertEqual(400, self.json_request("POST", f"/api/jobs/{job_id}/review", review)[0])
        self.assertEqual([], self.server.repository.get_job(job_id)["review_history"])

    def test_bounded_extraction_remains_reviewable_with_escaped_unicode(self):
        detail = self.upload()
        self.server.workers.process_once()
        job_id = detail["job"]["id"]
        candidate = payload()
        template = candidate["members"][0]
        candidate["members"] = []
        for index in range(20):
            member = {key: "é" * 1800 for key in template}
            member.update(row_number=index + 1, nik_raw=f"{index + 1:016}")
            candidate["members"].append(member)
        review = {"expected_version": self.server.repository.get_job(job_id)["job"]["version"], "decision": "NEEDS_REVIEW", "extraction": candidate, "comment": "Large synthetic Unicode review"}
        self.assertGreater(len(json.dumps(review).encode()), 2 * 1024 * 1024)
        status, _, saved = self.json_request("POST", f"/api/jobs/{job_id}/review", review)
        self.assertEqual(200, status, saved)
        self.assertEqual(candidate, saved["extraction"])

    def test_static_traversal_cannot_read_runtime_or_source_code(self):
        for path in ("/../kkpilot/config.py", "/%2e%2e/kkpilot/config.py", "/runtime/pilot.sqlite3", "/api/jobs/../../config"):
            status, _, _ = self.json_request("GET", path)
            self.assertEqual(404, status)

    def test_list_filter_treats_sql_as_literal(self):
        self.upload(case_id="literal'); DROP TABLE jobs;--")
        status, _, result = self.json_request("GET", "/api/jobs?query=DROP%20TABLE")
        self.assertEqual(200, status)
        self.assertEqual(1, len(result["jobs"]))
        status, _, result = self.json_request("GET", "/api/jobs?status=ACCEPTED")
        self.assertEqual([], result["jobs"])


class ConfigTests(unittest.TestCase):
    def test_nonloopback_and_unbounded_workers_refused(self):
        for kwargs in ({"host": "0.0.0.0"}, {"host": "localhost"}, {"workers": 0}, {"workers": 100}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                Config(**kwargs)

    def test_ambient_cloud_credentials_do_not_enable_live_mode(self):
        with patch.dict(os.environ, {"OCI_CONFIG_FILE": "never-read", "OCI_CLI_PROFILE": "never-read"}, clear=True):
            config = from_environment()
        self.assertEqual({"reference_dir": None}, config.adapter_config())
        self.assertEqual(["demo", "reference", "oci"], [mode["id"] for mode in config.modes()])


if __name__ == "__main__":
    unittest.main()
