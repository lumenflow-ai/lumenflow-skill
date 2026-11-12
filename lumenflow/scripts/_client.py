"""Safe standard-library client for the Lumenflow public API."""

from __future__ import annotations

import json
import math
from http.client import HTTPException
import os
import random
import re
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    OpenerDirector,
    Request,
    build_opener,
)

try:
    from ._version import VERSION
    from ._transport import REQUEST_SECONDS, read_bounded, save_download
    from ._responses import validate_endpoint_response
    from ._contract import (
        LumenflowError,
        new_idempotency_key,
        validate_base_url,
        validate_download_url,
        validate_idempotency_key,
        validate_public_response,
        validate_request_payload,
        validate_resource_id,
    )
except ImportError:
    from _version import VERSION
    from _transport import REQUEST_SECONDS, read_bounded, save_download
    from _responses import validate_endpoint_response
    from _contract import (
        LumenflowError,
        new_idempotency_key,
        validate_base_url,
        validate_download_url,
        validate_idempotency_key,
        validate_public_response,
        validate_request_payload,
        validate_resource_id,
    )

DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_API_BASE_URL = "https://api.lumenflow.net/open/v1"
OFFICIAL_API_HOSTS = frozenset({"api.lumenflow.net"})
MAX_JSON_RESPONSE_BYTES = 4 * 1024 * 1024
RETRYABLE_STATUS_CODES = frozenset({429, 502, 503, 504})
MAX_SEEDANCE25_IMAGE_BYTES = 30 * 1024 * 1024
MAX_SEEDANCE25_AUDIO_BYTES = 15 * 1024 * 1024
SEEDANCE25_REFERENCE_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
}


@dataclass(frozen=True)
class ClientConfig:
    base_url: str
    api_key: str = field(repr=False)
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_retries: int = DEFAULT_MAX_RETRIES
    user_agent: str = f"lumenflow-agent-skill/{VERSION}"
    allowed_hosts: frozenset[str] = field(
        default_factory=lambda: OFFICIAL_API_HOSTS,
        repr=False,
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_url", validate_base_url(self.base_url))
        allowed_hosts = frozenset(
            host.strip().lower().rstrip(".")
            for host in self.allowed_hosts
            if host.strip()
        )
        object.__setattr__(self, "allowed_hosts", allowed_hosts)
        hostname = (urlsplit(self.base_url).hostname or "").lower().rstrip(".")
        if hostname not in allowed_hosts:
            raise LumenflowError(
                "invalid_configuration",
                "The public API host is not an approved Lumenflow host.",
            )
        if not self.api_key.strip():
            raise LumenflowError(
                "authentication_required",
                "LUMENFLOW_API_KEY is required.",
                status=401,
            )
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,512}", self.api_key):
            raise LumenflowError("invalid_configuration", "The API credential has an invalid format.")
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0 or self.timeout_seconds > 300:
            raise LumenflowError(
                "invalid_input",
                "Timeout must be greater than 0 and no more than 300 seconds.",
            )
        if self.max_retries < 0 or self.max_retries > 8:
            raise LumenflowError(
                "invalid_input",
                "Retry count must be between 0 and 8.",
            )

    @classmethod
    def from_env(
        cls,
        *,
        base_url: str | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> ClientConfig:
        return cls(
            base_url=(
                base_url
                or os.environ.get("LUMENFLOW_API_BASE_URL")
                or DEFAULT_API_BASE_URL
            ),
            api_key=os.environ.get("LUMENFLOW_API_KEY", ""),
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
        )


class SafeRedirectHandler(HTTPRedirectHandler):
    """Reject redirects for both authenticated API calls and bearer-ticket downloads."""

    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Mapping[str, str],
        newurl: str,
    ) -> Request | None:
        raise URLError("public request redirect refused")


class LumenflowClient:
    """Client exposing only the PRD-approved public capabilities."""

    def __init__(
        self,
        config: ClientConfig,
        *,
        opener: OpenerDirector | Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
        random_fn: Callable[[], float] = random.random,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self._opener = opener or build_opener(SafeRedirectHandler())
        self._sleep = sleep
        self._random = random_fn
        self._monotonic = monotonic
        self.last_retry_after: float | None = None
        self.last_request_id: str | None = None

    def session(self) -> Any:
        return self._request("GET", "/session")

    def create_empty(self, title: str, *, idempotency_key: str) -> Any:
        return self._request("POST", "/projects",
                             payload={"title": title, "creation_mode": "manual"},
                             idempotency_key=validate_idempotency_key(idempotency_key))

    def records(self, project_id: str, resource: str, *, after: str = "", limit: int = 50) -> Any:
        project_id = validate_resource_id(project_id, "project")
        if resource not in {"episodes", "scene-records", "asset-records", "failures"}:
            raise LumenflowError("invalid_input", "Unsupported resource.")
        if not 1 <= limit <= 100 or len(after) > 128:
            raise LumenflowError("invalid_input", "Invalid pagination.")
        return self._request("GET", f"/projects/{quote(project_id, safe='')}/{resource}?limit={limit}&after={quote(after, safe='')}")

    def review_materials(self, project_id: str, *, episode_from: int, episode_to: int, limit: int = 25, after: str = "") -> Any:
        project_id = validate_resource_id(project_id, "project")
        if any(type(number) is not int for number in (episode_from, episode_to, limit)):
            raise LumenflowError("invalid_input", "Episode numbers and limit must be integers.")
        if not (1 <= episode_from <= episode_to <= 999999 and episode_to - episode_from < 20 and 1 <= limit <= 50):
            raise LumenflowError("invalid_input", "Review allows 1–20 episodes and 1–50 scenes per page.")
        if not isinstance(after, str) or len(after) > 2048:
            raise LumenflowError("invalid_input", "Invalid review cursor.")
        data = self._request("GET", f"/projects/{quote(project_id, safe='')}/review-materials?episode_from={episode_from}&episode_to={episode_to}&limit={limit}&after={quote(after, safe='')}")
        if data["project_id"] != project_id or data["episode_from"] != episode_from or data["episode_to"] != episode_to:
            raise LumenflowError("unsafe_response_blocked", "The review response did not match the requested project and range.")
        return data

    def batch(self, project_id: str, kind: str, payload: Any, *, idempotency_key: str) -> Any:
        project_id = validate_resource_id(project_id, "project")
        paths = {"episodes": "episodes/batch", "assets": "asset-records/batch",
                 "scenes": "scenes/batch", "prompts": "scenes/prompts/batch"}
        if kind not in paths or not isinstance(payload, dict) or set(payload) != {"items"}:
            raise LumenflowError("invalid_input", "Batch requires an items object.")
        items = payload["items"]
        maximum = 20 if kind == "episodes" else 50
        if not isinstance(items, list) or not 1 <= len(items) <= maximum:
            raise LumenflowError("invalid_input", "Invalid batch size.")
        validate_request_payload(payload)
        if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > 1024 * 1024:
            raise LumenflowError("invalid_input", "Batch exceeds 1 MiB.")
        return self._request("PATCH" if kind == "prompts" else "POST",
                             f"/projects/{quote(project_id, safe='')}/{paths[kind]}",
                             payload=payload, idempotency_key=validate_idempotency_key(idempotency_key))

    def styles(self) -> Any:
        return self._request("GET", "/styles")

    def create_project(
        self,
        *,
        title: str,
        script: str,
        style_id: str | None = None,
        aspect_ratio: str | None = None,
        idempotency_key: str | None = None,
    ) -> Any:
        payload: dict[str, Any] = {"title": title, "script": script}
        if style_id:
            payload["style_id"] = style_id
        if aspect_ratio:
            payload["aspect_ratio"] = aspect_ratio
        return self._request(
            "POST",
            "/projects",
            payload=payload,
            idempotency_key=validate_idempotency_key(idempotency_key),
        )

    def parse_project(
        self,
        project_id: str,
        *,
        idempotency_key: str | None = None,
    ) -> Any:
        project_id = validate_resource_id(project_id, "project")
        return self._request(
            "POST",
            f"/projects/{quote(project_id, safe='')}/parse",
            payload={},
            idempotency_key=validate_idempotency_key(idempotency_key),
        )

    def progress(self, project_id: str) -> Any:
        project_id = validate_resource_id(project_id, "project")
        return self._request(
            "GET",
            f"/projects/{quote(project_id, safe='')}/progress",
        )

    def models(self, project_id: str) -> Any:
        project_id = validate_resource_id(project_id, "project")
        return self._request("GET", f"/projects/{quote(project_id, safe='')}/models")

    def generation_quote(self, project_id: str, selection_id: str, *, scene_ids: list[str] | None = None, retry_failed: bool = False) -> Any:
        project_id = validate_resource_id(project_id, "project")
        if not re.fullmatch(r"[a-f0-9]{64}",selection_id):
            raise LumenflowError("invalid_input", "Use a selection ID from the current project model list.")
        if scene_ids and retry_failed:
            raise LumenflowError("invalid_input", "Choose selected scenes or failed-scene retry.")
        payload = {"mode":"selected" if scene_ids else "retry_failed" if retry_failed else "all", "selection_id":selection_id}
        if scene_ids: payload["scene_ids"] = [validate_resource_id(s,"scene") for s in scene_ids]
        return self._request("POST", f"/projects/{quote(project_id, safe='')}/quotes", payload=payload)

    def generate(
        self,
        project_id: str,
        *,
        scene_ids: list[str] | None = None,
        retry_failed: bool = False,
        selection_id: str | None = None,
        quote_fingerprint: str | None = None,
        idempotency_key: str | None = None,
    ) -> Any:
        project_id = validate_resource_id(project_id, "project")
        if scene_ids and retry_failed:
            raise LumenflowError(
                "invalid_input",
                "Choose selected scenes or failed-scene retry, not both.",
            )
        payload: dict[str, Any]
        if scene_ids:
            payload = {
                "mode": "selected",
                "scene_ids": [
                    validate_resource_id(scene_id, "scene") for scene_id in scene_ids
                ],
            }
        elif retry_failed:
            payload = {"mode": "retry_failed"}
        else:
            payload = {"mode": "all"}
        if selection_id is not None:
            if not re.fullmatch(r"[a-f0-9]{64}",selection_id) or not isinstance(quote_fingerprint,str) or not re.fullmatch(r"spq1\.\d{10}\.[a-f0-9]{64}",quote_fingerprint):
                raise LumenflowError("invalid_input", "Selected-model generation requires the current confirmed quote.")
            payload.update(selection_id=selection_id, quote_fingerprint=quote_fingerprint)
        elif quote_fingerprint is not None:
            raise LumenflowError("invalid_input", "A quote requires its model selection.")
        return self._request(
            "POST",
            f"/projects/{quote(project_id, safe='')}/generations",
            payload=payload,
            idempotency_key=validate_idempotency_key(idempotency_key),
        )

    def assets(self, project_id: str) -> Any:
        project_id = validate_resource_id(project_id, "project")
        return self._request(
            "GET",
            f"/projects/{quote(project_id, safe='')}/assets",
        )

    def seedance25_capabilities(self) -> Any:
        return self._request("GET", "/workbenches/seedance-2.5/capabilities")

    def seedance25_upload(
        self,
        file_path: Path,
        *,
        idempotency_key: str | None = None,
    ) -> Any:
        try:
            path = file_path.expanduser().resolve(strict=True)
        except OSError:
            raise LumenflowError(
                "invalid_input",
                "The reference file could not be read.",
            ) from None
        if not path.is_file():
            raise LumenflowError("invalid_input", "The reference path is not a file.")
        mime_type = SEEDANCE25_REFERENCE_TYPES.get(path.suffix.lower())
        if mime_type is None:
            raise LumenflowError(
                "invalid_input",
                "Seedance 2.5 references must be JPG, PNG, WebP, MP3, or WAV files.",
            )
        try:
            size = path.stat().st_size
            maximum = MAX_SEEDANCE25_AUDIO_BYTES if mime_type.startswith("audio/") else MAX_SEEDANCE25_IMAGE_BYTES
            if size <= 0 or size > maximum:
                raise LumenflowError(
                    "invalid_input",
                    "A Seedance 2.5 reference must be nonempty; images allow 30 MiB and audio 15 MiB.",
                )
            content = path.read_bytes()
        except LumenflowError:
            raise
        except OSError:
            raise LumenflowError(
                "invalid_input",
                "The reference file could not be read.",
            ) from None
        boundary = f"lumenflow-{uuid.uuid4().hex}"
        body = _multipart_reference_body(
            boundary=boundary,
            original_name=path.name,
            filename=f"reference{path.suffix.lower()}",
            mime_type=mime_type,
            content=content,
        )
        return self._request(
            "POST",
            "/workbenches/seedance-2.5/assets",
            body=body,
            content_type=f"multipart/form-data; boundary={boundary}",
            idempotency_key=validate_idempotency_key(idempotency_key),
        )

    def seedance25_create_job(
        self,
        *,
        prompt: str,
        duration_seconds: int,
        aspect_ratio: str,
        asset_ids: list[str] | None = None,
        idempotency_key: str | None = None,
    ) -> Any:
        return self._request(
            "POST",
            "/workbenches/seedance-2.5/jobs",
            payload={
                "prompt": prompt,
                "duration_seconds": duration_seconds,
                "aspect_ratio": aspect_ratio,
                "asset_ids": [
                    validate_resource_id(asset_id, "Seedance 2.5 asset")
                    for asset_id in (asset_ids or [])
                ],
            },
            idempotency_key=validate_idempotency_key(idempotency_key),
        )

    def seedance25_job(self, job_id: str) -> Any:
        job_id = validate_resource_id(job_id, "Seedance 2.5 job")
        return self._request(
            "GET",
            f"/workbenches/seedance-2.5/jobs/{quote(job_id, safe='')}",
        )

    def seedance25_assets(self, job_id: str) -> Any:
        job_id = validate_resource_id(job_id, "Seedance 2.5 job")
        return self._request(
            "GET",
            f"/workbenches/seedance-2.5/jobs/{quote(job_id, safe='')}/assets",
        )

    def download(
        self,
        download_url: str,
        destination: Path,
        *,
        overwrite: bool = False,
        max_bytes: int | None = None,
    ) -> Path:
        validate_download_url(download_url, allowed_hosts=self.config.allowed_hosts)
        try:
            return save_download(self._opener, download_url, destination,
                                 user_agent=self.config.user_agent, timeout=self.config.timeout_seconds,
                                 overwrite=overwrite, max_bytes=max_bytes, monotonic=self._monotonic)
        except LumenflowError:
            raise
        except (HTTPError, URLError, OSError, HTTPException):
            raise LumenflowError("download_failed", "The result could not be downloaded safely.",
                                 retriable=True) from None

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping[str, Any] | None = None,
        body: bytes | None = None,
        content_type: str | None = None,
        idempotency_key: str | None = None,
    ) -> Any:
        if not path.startswith("/") or "://" in path:
            raise LumenflowError("invalid_input", "Invalid public API path.")
        if payload is not None:
            validate_request_payload(payload)
        if payload is not None and body is not None:
            raise LumenflowError(
                "invalid_input",
                "A request cannot contain two body formats.",
            )
        if idempotency_key is not None:
            idempotency_key = validate_idempotency_key(idempotency_key)
            if self.config.api_key in idempotency_key:
                raise LumenflowError("invalid_input", "Use an operation key, never a credential, for idempotency.")

        request_body = body
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.config.api_key}",
            "User-Agent": self.config.user_agent,
        }
        if payload is not None:
            request_body = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            headers["Content-Type"] = "application/json"
        elif body is not None:
            if not content_type:
                raise LumenflowError(
                    "invalid_input",
                    "A binary request requires a content type.",
                )
            headers["Content-Type"] = content_type
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key

        request = Request(
            f"{self.config.base_url}{path}",
            data=request_body,
            headers=headers,
            method=method,
        )

        deadline = self._monotonic() + REQUEST_SECONDS
        for attempt in range(self.config.max_retries + 1):
            try:
                with self._opener.open(
                    request,
                    timeout=min(self.config.timeout_seconds, max(0.001, deadline - self._monotonic())),
                ) as response:
                    self.last_request_id = _safe_request_id(_header(response, "X-Request-Id"), self.config.api_key)
                    self.last_retry_after = _parse_retry_after(
                        _header(response, "Retry-After")
                    )
                    data = _read_json(response, deadline=deadline, monotonic=self._monotonic)
                    validate_endpoint_response(method, path, data)
                    if self.config.api_key in json.dumps(data, ensure_ascii=False):
                        raise LumenflowError("unsafe_response_blocked", "A credential was present in the public response.")
                    return data
            except HTTPError as exc:
                error = _public_http_error(exc, self.config.api_key, deadline=deadline, monotonic=self._monotonic)
                self.last_request_id = error.request_id
                self.last_retry_after = error.retry_after
                if error.retriable and attempt < self.config.max_retries:
                    delay = error.retry_after if error.retry_after is not None else _backoff_seconds(attempt, self._random)
                    if delay < deadline - self._monotonic():
                        self._sleep(delay)
                        continue
                raise error from None
            except (URLError, OSError, HTTPException):
                delay = _backoff_seconds(attempt, self._random)
                if attempt < self.config.max_retries and delay < deadline - self._monotonic():
                    self._sleep(delay)
                    continue
                code = "operation_outcome_unknown" if idempotency_key else "service_unavailable"
                raise LumenflowError(code,
                    "The request outcome is unknown. Keep the original idempotency key and exact input; do not submit with a new key."
                    if idempotency_key else "Lumenflow is temporarily unavailable.",
                    retriable=not bool(idempotency_key), request_id=self.last_request_id) from None


def _multipart_reference_body(
    *,
    boundary: str,
    original_name: str,
    filename: str,
    mime_type: str,
    content: bytes,
) -> bytes:
    safe_name = original_name.replace("\r", "").replace("\n", "")
    parts = [
        f"--{boundary}\r\n".encode("ascii"),
        b'Content-Disposition: form-data; name="originalName"\r\n\r\n',
        safe_name.encode("utf-8"),
        b"\r\n",
        f"--{boundary}\r\n".encode("ascii"),
        (
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: {mime_type}\r\n\r\n"
        ).encode("ascii"),
        content,
        b"\r\n",
        f"--{boundary}--\r\n".encode("ascii"),
    ]
    return b"".join(parts)


def _read_json(response: Any, *, deadline: float, monotonic=time.monotonic) -> Any:
    raw = read_bounded(response, MAX_JSON_RESPONSE_BYTES, deadline=deadline, monotonic=monotonic)
    if len(raw) > MAX_JSON_RESPONSE_BYTES:
        raise LumenflowError(
            "invalid_response",
            "Lumenflow returned a response that was too large.",
        )
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                          parse_constant=lambda _: _reject_json())
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise LumenflowError(
            "invalid_response",
            "Lumenflow returned an invalid public response.",
        ) from None


def _reject_json():
    raise ValueError("Invalid public JSON")


def _unique_object(pairs):
    value = {}
    for key, child in pairs:
        if key in value:
            _reject_json()
        value[key] = child
    return value


def _safe_request_id(value: Any, api_key: str) -> str | None:
    if not isinstance(value, str) or api_key in value:
        return None
    return value if re.fullmatch(r"(?:req_[A-Za-z0-9_-]{1,100}|[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12})", value) else None


def _public_http_error(exc: HTTPError, api_key: str, *, deadline: float, monotonic=time.monotonic) -> LumenflowError:
    request_id = _safe_request_id(_header(exc, "X-Request-Id"), api_key)
    retry_after = _parse_retry_after(_header(exc, "Retry-After"))
    status = exc.code
    try:
        payload = _read_json(exc, deadline=deadline, monotonic=monotonic)
    except (LumenflowError, OSError, URLError, HTTPException):
        payload = {}
    finally:
        exc.close()
    error = payload.get("error", {}) if isinstance(payload, Mapping) else {}
    if not isinstance(error, Mapping):
        error = {}
    # Public code allowlist, never arbitrary server strings or raw messages.
    candidate = error.get("code")
    code = candidate if isinstance(candidate, str) and candidate in PUBLIC_ERROR_CODES and api_key not in candidate else _default_error_code(status)
    request_id = _safe_request_id(error.get("request_id"), api_key) or request_id
    retriable = status in RETRYABLE_STATUS_CODES and code in {"rate_limited", "service_unavailable", "result_not_ready"} and error.get("retriable", True) is not False
    return LumenflowError(code, _default_error_message(status), status=status,
                         retriable=retriable, request_id=request_id, retry_after=retry_after)


PUBLIC_ERROR_CODES = frozenset({
    "authentication_required", "token_invalid", "token_expired", "token_revoked", "scope_missing",
    "account_unavailable", "project_not_found", "asset_not_found", "reference_asset_not_found",
    "workbench_job_not_found", "workbench_job_not_ready", "project_state_conflict", "invalid_input",
    "content_rejected", "insufficient_credits", "rate_limited", "quota_exceeded", "generation_failed",
    "generation_confirmation_required", "result_not_ready", "service_unavailable", "unsafe_response_blocked",
    "request_failed", "idempotency_conflict", "idempotency_in_progress", "version_conflict",
    "canvas_not_found", "upload_rejected", "enhancement_failed",
    "quote_changed", "model_unavailable", "model_duration_incompatible", "resource_not_found",
    "revision_conflict", "scene_edit_conflict", "content_too_large", "invalid_idempotency_key", "operation_in_progress",
})


def _default_error_code(status: int) -> str:
    return {
        400: "invalid_input",
        401: "token_invalid",
        402: "insufficient_credits",
        403: "scope_missing",
        404: "project_not_found",
        409: "project_state_conflict",
        413: "invalid_input",
        429: "rate_limited",
        502: "service_unavailable",
        503: "service_unavailable",
        504: "service_unavailable",
    }.get(status, "request_failed")


def _default_error_message(status: int) -> str:
    return {
        400: "The request was not accepted.",
        401: "The API token is invalid or unavailable.",
        402: "The account does not have enough credits.",
        403: "The token does not have permission for this operation.",
        404: "The requested resource was not found.",
        409: "The project is not in a state that allows this operation.",
        413: "The request is too large.",
        429: "The request rate limit was reached.",
    }.get(status, "Lumenflow could not complete the request.")


def _header(response: Any, name: str) -> str | None:
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    value = headers.get(name)
    return str(value) if value is not None else None


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        delay = float(value)
        return delay if math.isfinite(delay) and delay >= 0 else None
    except ValueError:
        try:
            target = parsedate_to_datetime(value)
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
            return max(
                0.0,
                (target - datetime.now(timezone.utc)).total_seconds(),
            )
        except (TypeError, ValueError, OverflowError):
            return None


def _backoff_seconds(attempt: int, random_fn: Callable[[], float]) -> float:
    return min(30.0, (2**attempt) + random_fn())
