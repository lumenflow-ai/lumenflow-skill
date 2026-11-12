"""Regression cases from the public-release audit. No live service access."""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError
from urllib.request import Request

from test_client import FakeOpener, FakeResponse, config, http_error
from _client import LumenflowClient, LumenflowError, SafeRedirectHandler, _parse_retry_after
from _contract import validate_download_url
from _responses import validate_endpoint_response
from _transport import chunks
import lumenflow as cli

URL = "https://api.example.com/open/v1/downloads/dlg_" + "a" * 43


class RecoveryTests(unittest.TestCase):
    def test_key_generation_requires_neither_credentials_nor_network(self):
        output = io.StringIO()
        with patch.dict(os.environ, {}, clear=True), redirect_stdout(output):
            self.assertEqual(cli.main(["new-key"]), 0)
        self.assertRegex(json.loads(output.getvalue())["idempotency_key"], r"^[a-f0-9-]{36}$")

    def test_cli_requires_retained_keys_before_each_write(self):
        cases = [
            ["create", "--title", "test", "--script", "test script text"],
            ["parse", "prj_test"], ["generate", "prj_test"],
            ["workflow", "--title", "test", "--script", "test script text"],
            ["seedance25-upload", "image.png"],
            ["seedance25-create", "--prompt", "test", "--duration", "4", "--aspect-ratio", "16:9"],
        ]
        for args in cases:
            with self.subTest(command=args[0]), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                cli.build_parser().parse_args(args)

    def test_transport_uncertainty_preserves_caller_key_across_invocations(self):
        requests = []
        for _ in range(2):
            opener = FakeOpener([TimeoutError("private socket error")])
            client = LumenflowClient(config(max_retries=0), opener=opener)
            with self.assertRaises(LumenflowError) as caught:
                client.generate("prj_test", idempotency_key="retained-operation-key")
            self.assertEqual(caught.exception.code, "operation_outcome_unknown")
            self.assertNotIn("private socket", str(caught.exception))
            requests.append(opener.requests[0])
        self.assertEqual(requests[0].data, requests[1].data)
        self.assertEqual(requests[0].get_header("Idempotency-key"), requests[1].get_header("Idempotency-key"))

    def test_library_cannot_generate_without_key(self):
        opener = FakeOpener([])
        with self.assertRaises(LumenflowError):
            LumenflowClient(config(), opener=opener).generate("prj_test")
        self.assertFalse(opener.requests)


class SafeDownloadTests(unittest.TestCase):
    def test_old_partial_symlink_cannot_overwrite_other_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            victim = root / "victim"; victim.write_bytes(b"original")
            (root / ".result.mp4.part").symlink_to(victim)
            client = LumenflowClient(config(), opener=FakeOpener([FakeResponse(b"video")]))
            client.download(URL, root / "result.mp4")
            self.assertEqual(victim.read_bytes(), b"original")
            self.assertEqual((root / "result.mp4").read_bytes(), b"video")

    def test_destination_symlink_refused_even_with_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); target = root / "victim"; target.write_bytes(b"original")
            output = root / "result.mp4"; output.symlink_to(target)
            opener = FakeOpener([])
            with self.assertRaises(LumenflowError):
                LumenflowClient(config(), opener=opener).download(URL, output, overwrite=True)
            self.assertEqual(target.read_bytes(), b"original")
            self.assertFalse(opener.requests)

    def test_target_created_during_download_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.mp4"
            class RacingResponse(FakeResponse):
                def read(self, n=-1):
                    output.write_bytes(b"other process")
                    return super().read(n)
            client = LumenflowClient(config(), opener=FakeOpener([RacingResponse(b"video")]))
            with self.assertRaises(LumenflowError) as caught:
                client.download(URL, output)
            self.assertEqual(caught.exception.code, "file_exists")
            self.assertEqual(output.read_bytes(), b"other process")
            self.assertFalse(list(Path(directory).glob(".lumenflow-*.part")))

    def test_download_hosts_paths_ports_and_credentials_fail_closed(self):
        invalid = ["https://127.0.0.1/result", "https://[::1]/result", "https://evil.example/result",
                   URL.replace("api.example.com", "api.lumenflow.net.evil.example"),
                   URL.replace("api.example.com", "api.lumenflow.net:444"),
                   URL + "?secret=foo", URL + "#foo", "https://api.lumenflow.net/private",
                   "https://user@api.lumenflow.net/open/v1/downloads/dlg_" + "a" * 43]
        for url in invalid:
            with self.subTest(url=url), self.assertRaises(LumenflowError): validate_download_url(url)
        validate_download_url(URL, allowed_hosts={"api.example.com"})

    def test_all_redirects_refused_without_forwarding_grant_or_bearer(self):
        for target in ("http://127.0.0.1/result", URL, "https://evil.example/result"):
            for headers in ({}, {"Authorization": "Bearer synthetic"}):
                with self.subTest(target=target, auth=bool(headers)), self.assertRaises(URLError):
                    SafeRedirectHandler().redirect_request(Request(URL, headers=headers), None, 302, "", {}, target)

    def test_default_size_limit_and_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            opener = FakeOpener([FakeResponse(b"", headers={"Content-Length": "500000001"})])
            with self.assertRaises(LumenflowError) as caught:
                LumenflowClient(config(), opener=opener).download(URL, root / "result")
            self.assertEqual(caught.exception.code, "download_too_large")
            self.assertFalse(list(root.iterdir()))

    def test_truncated_download_does_not_publish(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            opener = FakeOpener([FakeResponse(b"short", headers={"Content-Length": "10"})])
            with self.assertRaises(LumenflowError):
                LumenflowClient(config(), opener=opener).download(URL, root / "result")
            self.assertFalse(list(root.iterdir()))


class ResponseSafetyTests(unittest.TestCase):
    def test_error_code_message_and_request_id_cannot_echo_key(self):
        secret = "synthetic-test-credential"
        error = http_error(400, {"error": {"code": secret, "message": secret, "request_id": "req_" + secret}},
                           headers={"X-Request-Id": "req_" + secret})
        with self.assertRaises(LumenflowError) as caught:
            LumenflowClient(config(api_key=secret), opener=FakeOpener([error])).session()
        self.assertNotIn(secret, json.dumps(caught.exception.as_dict()))
        self.assertEqual(caught.exception.code, "invalid_input")
        # Keep actionable public quote/version conflicts without trusting messages.
        for code in ("quote_changed", "revision_conflict", "scene_edit_conflict", "operation_in_progress"):
            error = http_error(409, {"error": {"code": code, "message": "private detail"}})
            with self.assertRaises(LumenflowError) as caught:
                LumenflowClient(config(), opener=FakeOpener([error])).session()
            self.assertEqual(caught.exception.code, code)
            self.assertNotIn("private detail", str(caught.exception))

    def test_success_response_cannot_echo_key_in_allowed_string(self):
        with self.assertRaises(LumenflowError):
            LumenflowClient(config(), opener=FakeOpener([FakeResponse({"project_id":"prj_test", "status":"test_key"})])).progress("prj_test")

    def test_unknown_fields_nested_objects_and_routes_rejected(self):
        cases = [
            ("GET", "/session", {"authenticated":True,"unexpected":"private"}),
            ("GET", "/styles", {"styles":[{"name":"test", "extra":"private"}]}),
            ("GET", "/projects/prj_test/progress", {"scene_items":[{"status":{"value":"private"}}]}),
            ("GET", "/projects/prj_test/episodes", {"items":[{"script_content":"private"}]}),
            ("POST", "/projects/prj_test/scenes/batch", {"items":[{"scene_id":"scn_test", "extra":1}]}),
            ("GET", "/workbenches/seedance-2.5/capabilities", {"reference_limits":{"extra":1}}),
            ("GET", "/workbenches/seedance-2.5/jobs/swj_test", {"reference_counts":{"extra":1}}),
            ("GET", "/unknown", {}),
        ]
        for method, route, body in cases:
            with self.subTest(route=route), self.assertRaises(LumenflowError):
                validate_endpoint_response(method, route, body)

    def test_current_basic_and_workbench_dtos_are_accepted(self):
        cases = [
            ("GET", "/session", {"authenticated":True,"scopes":["project:read"],"expires_at":"2026-12-01T00:00:00Z"}),
            ("GET", "/projects/prj_test/scene-records", {"project_id":"prj_test","items":[{"id":"scn_test","episode_id":None,"episode_number":None,"scene_number":1,"status":"draft","edit_version":"v1"}],"next_cursor":None}),
            ("GET", "/workbenches/seedance-2.5/jobs/swj_test/assets", {"job_id":"swj_test","assets":[{"asset_id":"out_test","filename":"result.mp4","size_bytes":5,"content_type":"video/mp4","download_url":URL}]}),
        ]
        for method, route, body in cases: validate_endpoint_response(method, route, body)

    def test_large_retry_after_returns_without_early_retry_or_long_sleep(self):
        sleeps = []
        error = http_error(429, {"error":{"code":"rate_limited"}}, headers={"Retry-After":"999999"})
        opener = FakeOpener([error])
        with self.assertRaises(LumenflowError):
            LumenflowClient(config(), opener=opener, sleep=sleeps.append).session()
        self.assertEqual(sleeps, [])
        self.assertEqual(len(opener.requests), 1)

    def test_nonfinite_retry_after_rejected(self):
        for value in ("NaN", "inf", "-inf", "-1"):
            self.assertIsNone(_parse_retry_after(value))

    def test_read_timeout_retries_within_budget_and_is_sanitized(self):
        sleeps = []
        opener = FakeOpener([TimeoutError("private detail"), FakeResponse({"authenticated":True})])
        result = LumenflowClient(config(), opener=opener, sleep=sleeps.append).session()
        self.assertTrue(result["authenticated"])
        self.assertEqual(len(sleeps), 1)

    def test_trickle_read_has_total_deadline(self):
        clock = [0]
        class Trickle:
            def read(self, n):
                clock[0] += 2
                return b"x"
        with self.assertRaises(TimeoutError):
            list(chunks(Trickle(), deadline=3, monotonic=lambda:clock[0]))

    def test_duplicate_nonfinite_and_excessively_nested_json_rejected(self):
        for raw in (b'{"authenticated":true,"authenticated":false}',
                    b'{"authenticated":NaN}', b'[' * 1500 + b'0' + b']' * 1500):
            with self.subTest(raw_size=len(raw)), self.assertRaises(LumenflowError):
                LumenflowClient(config(), opener=FakeOpener([FakeResponse(raw)])).session()

    def test_nested_allowed_field_cannot_hide_extra_metadata(self):
        value = {"authenticated": {"nested": "private"}}
        with self.assertRaises(LumenflowError):
            LumenflowClient(config(), opener=FakeOpener([FakeResponse(value)])).session()

    def test_invalid_api_credential_and_nonstandard_port_fail_before_network(self):
        for overrides in ({"api_key":"synthetic\r\nInjected: yes"},
                          {"base_url":"https://api.example.com:444/open/v1"},
                          {"timeout_seconds":float("nan")}):
            with self.subTest(overrides=list(overrides)), self.assertRaises(LumenflowError): config(**overrides)
