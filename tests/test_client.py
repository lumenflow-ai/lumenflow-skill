from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Self
from urllib.error import HTTPError, URLError
from urllib.request import Request

SCRIPTS_DIR = Path(__file__).parents[1] / "lumenflow" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from _client import (
    ClientConfig,
    LumenflowClient,
    LumenflowError,
    SafeRedirectHandler,
    validate_download_url,
    validate_idempotency_key,
    validate_public_response,
    validate_request_payload,
    validate_resource_id,
)


class FakeResponse:
    def __init__(
        self,
        body: bytes | dict | list = b"",
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode("utf-8")
        self._body = io.BytesIO(body)
        self.headers = headers or {}

    def read(self, size: int = -1) -> bytes:
        return self._body.read(size)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None


class FakeOpener:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.requests: list[object] = []
        self.timeouts: list[float] = []

    def open(self, request: object, *, timeout: float) -> FakeResponse:
        self.requests.append(request)
        self.timeouts.append(timeout)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response  # type: ignore[return-value]


def http_error(
    status: int,
    payload: dict,
    *,
    headers: dict[str, str] | None = None,
) -> HTTPError:
    return HTTPError(
        "https://api.example.com/open/v1/test",
        status,
        "failure",
        headers or {},
        io.BytesIO(json.dumps(payload).encode("utf-8")),
    )


def config(**overrides: object) -> ClientConfig:
    values: dict[str, object] = {
        "base_url": "https://api.example.com/open/v1",
        "api_key": "test_key",
        "timeout_seconds": 7.0,
        "max_retries": 2,
        "allowed_hosts": frozenset({"api.example.com"}),
    }
    values.update(overrides)
    return ClientConfig(**values)  # type: ignore[arg-type]


def request_headers(request: object) -> dict[str, str]:
    return {key.lower(): value for key, value in request.header_items()}  # type: ignore[attr-defined]


class ClientConfigTests(unittest.TestCase):
    def test_config_hides_api_key_from_repr(self) -> None:
        value = config()
        self.assertNotIn("test_key", repr(value))

    def test_base_url_requires_https_open_v1(self) -> None:
        for value in (
            "http://api.example.com/open/v1",
            "https://api.example.com/api",
            "https://user:pass@api.example.com/open/v1",
            "https://api.example.com/open/v1?debug=1",
        ):
            with self.subTest(value=value), self.assertRaises(LumenflowError):
                config(base_url=value)

    def test_retry_limits_are_bounded(self) -> None:
        with self.assertRaises(LumenflowError):
            config(max_retries=9)

    def test_unapproved_api_host_is_rejected(self) -> None:
        with self.assertRaises(LumenflowError) as caught:
            config(
                base_url="https://evil.example/open/v1",
                allowed_hosts=frozenset({"api.example.com"}),
            )
        self.assertEqual(
            caught.exception.code,
            "invalid_configuration",
        )


class ClientSecurityTests(unittest.TestCase):
    def test_session_sends_bearer_only_to_public_base(self) -> None:
        opener = FakeOpener(
            [
                FakeResponse(
                    {"authenticated": True},
                    headers={"X-Request-Id": "req_1"},
                )
            ]
        )
        client = LumenflowClient(config(), opener=opener)

        result = client.session()

        request = opener.requests[0]
        headers = request_headers(request)
        self.assertEqual(result, {"authenticated": True})
        self.assertEqual(
            request.full_url,  # type: ignore[attr-defined]
            "https://api.example.com/open/v1/session",
        )
        self.assertEqual(
            headers["authorization"],
            "Bearer test_key",
        )
        self.assertEqual(client.last_request_id, "req_1")

    def test_forbidden_request_identity_fields_are_rejected(self) -> None:
        for key in ("user_id", "owner-id", "actAs", "impersonate_user_id"):
            with self.subTest(key=key):
                with self.assertRaises(LumenflowError) as caught:
                    validate_request_payload({"nested": {key: "usr_other"}})
                self.assertEqual(caught.exception.code, "forbidden_parameter")

    def test_sensitive_response_fields_are_blocked_recursively(self) -> None:
        for key in (
            "system_prompt",
            "promptTemplate",
            "provider_id",
            "channel-id",
            "environment_variables",
        ):
            with self.subTest(key=key):
                with self.assertRaises(LumenflowError) as caught:
                    validate_public_response({"data": [{key: "hidden"}]})
                self.assertEqual(
                    caught.exception.code,
                    "unsafe_response_blocked",
                )

    def test_sensitive_http_error_is_replaced_and_token_redacted(self) -> None:
        error = http_error(
            400,
            {
                "error": {
                    "code": "invalid_input",
                    "message": (
                        "provider failed with api key test_key and stack trace"
                    ),
                }
            },
        )
        client = LumenflowClient(
            config(max_retries=0),
            opener=FakeOpener([error]),
        )

        with self.assertRaises(LumenflowError) as caught:
            client.session()

        message = caught.exception.message
        self.assertNotIn("test_key", message)
        self.assertNotIn("provider", message.lower())
        self.assertNotIn("stack", message.lower())

    def test_signed_url_in_error_message_is_not_exposed(self) -> None:
        error = http_error(
            400,
            {
                "error": {
                    "code": "invalid_input",
                    "message": ("failed at https://cdn.example.com/file?sig=private"),
                }
            },
        )
        client = LumenflowClient(
            config(max_retries=0),
            opener=FakeOpener([error]),
        )

        with self.assertRaises(LumenflowError) as caught:
            client.session()

        self.assertNotIn("https://", caught.exception.message)
        self.assertNotIn("sig=", caught.exception.message)

    def test_authenticated_cross_origin_redirect_is_refused(self) -> None:
        request = Request(
            "https://api.example.com/open/v1/session",
            headers={"Authorization": "Bearer hidden"},
        )

        with self.assertRaises(URLError):
            SafeRedirectHandler().redirect_request(
                request,
                None,
                302,
                "Found",
                {},
                "https://other.example.com/open/v1/session",
            )

    def test_invalid_ids_and_idempotency_keys_are_rejected(self) -> None:
        for value in ("../other", "x", "id/other", "id?debug=1"):
            with self.subTest(value=value), self.assertRaises(LumenflowError):
                validate_resource_id(value, "project")
        with self.assertRaises(LumenflowError):
            validate_idempotency_key("short")

    def test_download_url_rejects_credentials_and_fragments(self) -> None:
        for value in (
            "http://files.example.com/result.mp4",
            "https://user:pass@files.example.com/result.mp4",
            "https://files.example.com/result.mp4#secret",
        ):
            with self.subTest(value=value), self.assertRaises(LumenflowError):
                validate_download_url(value)


class ClientRetryTests(unittest.TestCase):
    def test_rate_limit_respects_retry_after_and_preserves_idempotency(self) -> None:
        sleeps: list[float] = []
        opener = FakeOpener(
            [
                http_error(
                    429,
                    {
                        "error": {
                            "code": "rate_limited",
                            "message": "Slow down.",
                            "retriable": True,
                        }
                    },
                    headers={"Retry-After": "2"},
                ),
                FakeResponse({"project_id": "prj_123", "status": "created"}),
            ]
        )
        client = LumenflowClient(
            config(),
            opener=opener,
            sleep=sleeps.append,
            random_fn=lambda: 0.0,
        )

        client.create_project(
            title="Episode 1",
            script="A script long enough.",
            idempotency_key="idem-12345678",
        )

        self.assertEqual(sleeps, [2.0])
        first = request_headers(opener.requests[0])
        second = request_headers(opener.requests[1])
        self.assertEqual(first["idempotency-key"], "idem-12345678")
        self.assertEqual(second["idempotency-key"], "idem-12345678")

    def test_non_retryable_400_is_not_retried(self) -> None:
        sleeps: list[float] = []
        opener = FakeOpener(
            [
                http_error(
                    400,
                    {
                        "error": {
                            "code": "invalid_input",
                            "message": "Bad request.",
                        }
                    },
                )
            ]
        )
        client = LumenflowClient(
            config(),
            opener=opener,
            sleep=sleeps.append,
        )

        with self.assertRaises(LumenflowError) as caught:
            client.session()

        self.assertEqual(caught.exception.code, "invalid_input")
        self.assertEqual(len(opener.requests), 1)
        self.assertEqual(sleeps, [])

    def test_network_failure_retries_then_returns_public_error(self) -> None:
        sleeps: list[float] = []
        opener = FakeOpener(
            [URLError("internal hostname"), URLError("internal hostname")]
        )
        client = LumenflowClient(
            config(max_retries=1),
            opener=opener,
            sleep=sleeps.append,
            random_fn=lambda: 0.0,
        )

        with self.assertRaises(LumenflowError) as caught:
            client.styles()

        self.assertEqual(caught.exception.code, "service_unavailable")
        self.assertNotIn("hostname", caught.exception.message)
        self.assertEqual(sleeps, [1.0])


class ClientContractTests(unittest.TestCase):
    def test_capability_methods_use_only_documented_paths(self) -> None:
        opener = FakeOpener([FakeResponse({}) for _ in range(6)])
        client = LumenflowClient(config(), opener=opener)

        client.styles()
        client.create_project(
            title="Episode",
            script="A complete script.",
            idempotency_key="idem-create-123",
        )
        client.parse_project(
            "prj_123",
            quote_fingerprint="spq1.1800000120." + "a"*64,
            idempotency_key="idem-parse-123",
        )
        client.progress("prj_123")
        client.generate(
            "prj_123",
            scene_ids=["scn_1"],
            quote_fingerprint="spq1.1800000120." + "a"*64, selection_id="b"*64,
            idempotency_key="idem-generate-123",
        )
        client.assets("prj_123")

        self.assertEqual(
            [
                request.full_url.removeprefix("https://api.example.com/open/v1")
                for request in opener.requests
            ],
            [
                "/styles",
                "/projects",
                "/projects/prj_123/parse",
                "/projects/prj_123/progress",
                "/projects/prj_123/generations",
                "/projects/prj_123/assets",
            ],
        )

    def test_seedance25_methods_use_only_the_isolated_workbench_paths(self) -> None:
        opener = FakeOpener([FakeResponse({}) for _ in range(4)])
        client = LumenflowClient(config(), opener=opener)

        client.seedance25_capabilities()
        client.seedance25_create_job(
            prompt="A user-authored prompt.",
            duration_seconds=8,
            aspect_ratio="16:9",
            asset_ids=["swa_123"],
            quote_fingerprint="spq1.1800000120." + "a"*64,
            idempotency_key="seedance-create-123",
        )
        client.seedance25_job("swj_123")
        client.seedance25_assets("swj_123")

        self.assertEqual(
            [
                request.full_url.removeprefix("https://api.example.com/open/v1")
                for request in opener.requests
            ],
            [
                "/workbenches/seedance-2.5/capabilities",
                "/workbenches/seedance-2.5/jobs",
                "/workbenches/seedance-2.5/jobs/swj_123",
                "/workbenches/seedance-2.5/jobs/swj_123/assets",
            ],
        )

    def test_seedance25_upload_is_local_bounded_multipart(self) -> None:
        opener = FakeOpener([FakeResponse({"asset_id": "swa_123"})])
        client = LumenflowClient(config(), opener=opener)
        with tempfile.TemporaryDirectory() as temp_dir:
            reference = Path(temp_dir) / "角色.png"
            reference.write_bytes(b"png-bytes")

            client.seedance25_upload(
                reference,
                idempotency_key="seedance-upload-123",
            )

        request = opener.requests[0]
        headers = request_headers(request)
        body = request.data  # type: ignore[attr-defined]
        self.assertEqual(
            request.full_url,  # type: ignore[attr-defined]
            "https://api.example.com/open/v1/workbenches/seedance-2.5/assets",
        )
        self.assertTrue(headers["content-type"].startswith("multipart/form-data; boundary="))
        self.assertEqual(headers["idempotency-key"], "seedance-upload-123")
        self.assertIn("角色.png".encode(), body)
        self.assertIn(b"Content-Type: image/png", body)

    def test_seedance25_upload_rejects_non_media_before_network(self) -> None:
        opener = FakeOpener([])
        client = LumenflowClient(config(), opener=opener)
        with tempfile.TemporaryDirectory() as temp_dir:
            secret = Path(temp_dir) / "secret.env"
            secret.write_text("TOKEN=hidden", encoding="utf-8")

            with self.assertRaises(LumenflowError) as caught:
                client.seedance25_upload(secret)

        self.assertEqual(caught.exception.code, "invalid_input")
        self.assertEqual(opener.requests, [])


class ClientDownloadTests(unittest.TestCase):
    def test_download_never_sends_bearer_token(self) -> None:
        opener = FakeOpener([FakeResponse(b"video-bytes")])
        client = LumenflowClient(config(), opener=opener)
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "scene.mp4"

            saved = client.download(
                "https://api.example.com/open/v1/downloads/dlg_" + "a" * 43,
                destination,
            )

            self.assertEqual(saved.read_bytes(), b"video-bytes")
            headers = request_headers(opener.requests[0])
            self.assertNotIn("authorization", headers)

    def test_download_refuses_overwrite(self) -> None:
        opener = FakeOpener([FakeResponse(b"new")])
        client = LumenflowClient(config(), opener=opener)
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "scene.mp4"
            destination.write_bytes(b"existing")

            with self.assertRaises(LumenflowError) as caught:
                client.download(
                    "https://api.example.com/open/v1/downloads/dlg_" + "a" * 43,
                    destination,
                )

            self.assertEqual(caught.exception.code, "file_exists")
            self.assertEqual(destination.read_bytes(), b"existing")
            self.assertEqual(opener.requests, [])

    def test_download_limit_removes_partial_file(self) -> None:
        opener = FakeOpener([FakeResponse(b"too-large")])
        client = LumenflowClient(config(), opener=opener)
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "scene.mp4"

            with self.assertRaises(LumenflowError) as caught:
                client.download(
                    "https://api.example.com/open/v1/downloads/dlg_" + "a" * 43,
                    destination,
                    max_bytes=3,
                )

            self.assertEqual(caught.exception.code, "download_too_large")
            self.assertFalse(destination.exists())
            self.assertFalse((Path(temp_dir) / ".scene.mp4.part").exists())


if __name__ == "__main__":
    unittest.main()
