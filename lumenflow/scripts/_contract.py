"""Public contract validation shared by the Lumenflow Skill client."""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

RESOURCE_ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{2,127}$")
IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")

FORBIDDEN_REQUEST_KEYS = frozenset(
    {
        "act_as",
        "admin",
        "impersonate",
        "impersonate_user_id",
        "owner_id",
        "user_id",
    }
)

FORBIDDEN_RESPONSE_KEYS = frozenset(
    {
        "api_key",
        "bucket",
        "channel",
        "channel_id",
        "config",
        "configuration",
        "credential",
        "credentials",
        "database_url",
        "debug",
        "environment",
        "environment_variables",
        "feature_flags",
        "internal",
        "internal_model_id",
        "internal_prompt",
        "internal_prompt_id",
        "model_id",
        "model_routing",
        "prompt_template",
        "prompt_template_id",
        "prompt",
        "prompts",
        "provider",
        "provider_id",
        "queue",
        "raw_error",
        "routing",
        "secret",
        "server_path",
        "script",
        "script_content",
        "sql",
        "stack",
        "stack_trace",
        "stacktrace",
        "system_config",
        "system_prompt",
        "system_prompt_id",
        "storage_path",
        "upstream_error",
    }
)

SENSITIVE_MESSAGE_TERMS = (
    "system prompt",
    "system_prompt",
    "prompt template",
    "prompt_template",
    "provider",
    "channel_id",
    "database",
    "stack trace",
    "stacktrace",
    "sql ",
    "api key",
    "api_key",
    "secret",
    "http://",
    "https://",
)


class LumenflowError(Exception):
    """Public, sanitized failure returned to the CLI."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int | None = None,
        retriable: bool = False,
        request_id: str | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.retriable = retriable
        self.request_id = request_id
        self.retry_after = retry_after

    def as_dict(self) -> dict[str, Any]:
        error: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "retriable": self.retriable,
        }
        if self.status is not None:
            error["status"] = self.status
        if self.request_id:
            error["request_id"] = self.request_id
        if self.retry_after is not None:
            error["retry_after"] = self.retry_after
        return {"ok": False, "error": error}


def require_quote(value: str | None) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"spq1\.\d{10}\.[a-f0-9]{64}", value):
        raise LumenflowError("quote_changed", "Get a current quote and confirm the displayed credits first.")
    return value


def validate_base_url(value: str) -> str:
    value = value.strip().rstrip("/")
    if not value:
        raise LumenflowError(
            "configuration_required",
            "LUMENFLOW_API_BASE_URL is required until the public endpoint is released.",
        )
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        raise LumenflowError("invalid_configuration", "Invalid public API URL.") from None
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
        or port not in (None, 443)
        or any(c.isspace() or ord(c) < 32 for c in value)
    ):
        raise LumenflowError(
            "invalid_configuration",
            "The public API base URL must be an HTTPS URL without credentials, query, or fragment.",
        )
    if parts.path.rstrip("/") != "/open/v1":
        raise LumenflowError(
            "invalid_configuration",
            "The public API base URL must end with /open/v1.",
        )
    return value


def validate_download_url(value: str, *, allowed_hosts=frozenset({"api.lumenflow.net"})) -> str:
    try:
        parts = urlsplit(value)
        port = parts.port
    except (TypeError, ValueError):
        raise LumenflowError("invalid_download_url", "Invalid public download URL.") from None
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.fragment
        or parts.query
        or port not in (None, 443)
        or parts.hostname not in allowed_hosts
        or any(c.isspace() or ord(c) < 32 for c in value)
        or not re.fullmatch(r"/open/v1/(?:downloads/dlg_|canvas-downloads/)[A-Za-z0-9_-]{43}", parts.path)
    ):
        raise LumenflowError(
            "invalid_download_url",
            "The download URL is not a valid HTTPS result URL.",
        )
    return value


def validate_resource_id(value: str, kind: str) -> str:
    if not isinstance(value, str) or not RESOURCE_ID_PATTERN.fullmatch(value):
        raise LumenflowError(
            "invalid_input",
            f"Invalid public {kind} ID.",
        )
    return value


def validate_idempotency_key(value: str) -> str:
    if not isinstance(value, str) or not IDEMPOTENCY_KEY_PATTERN.fullmatch(value):
        raise LumenflowError(
            "invalid_input",
            "An explicit idempotency key of 8-128 safe ASCII characters is required. Run new-key once and retain it for recovery.",
        )
    return value


def new_idempotency_key() -> str:
    return str(uuid.uuid4())


def validate_request_payload(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if _normalize_key(str(key)) in FORBIDDEN_REQUEST_KEYS:
                raise LumenflowError(
                    "forbidden_parameter",
                    f"Public requests cannot include {key}.",
                )
            validate_request_payload(child)
    elif isinstance(value, list):
        for child in value:
            validate_request_payload(child)


def validate_public_response(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if _normalize_key(str(key)) in FORBIDDEN_RESPONSE_KEYS:
                raise LumenflowError(
                    "unsafe_response_blocked",
                    "Lumenflow returned a field that is not allowed in the public API.",
                    retriable=False,
                )
            validate_public_response(child)
    elif isinstance(value, list):
        for child in value:
            validate_public_response(child)


def sanitize_message(message: str, api_key: str) -> str:
    message = message.replace(api_key, "[REDACTED]").strip()
    lowered = message.lower()
    if any(term in lowered for term in SENSITIVE_MESSAGE_TERMS):
        return "The request failed. Use the request ID when contacting support."
    if len(message) > 300:
        return f"{message[:297]}..."
    return message or "The request failed."


def _normalize_key(value: str) -> str:
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
