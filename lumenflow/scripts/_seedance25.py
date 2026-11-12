"""Isolated Seedance 2.5 workflow orchestration."""

from __future__ import annotations

import argparse
import math
import random
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

try:
    from ._client import (
        LumenflowClient,
        LumenflowError,
        validate_idempotency_key,
    )
    from ._downloads import download_seedance25_assets
except ImportError:
    from _client import (
        LumenflowClient,
        LumenflowError,
        validate_idempotency_key,
    )
    from _downloads import download_seedance25_assets

SEEDANCE25_ASPECT_RATIOS = frozenset(
    {"16:9", "9:16", "1:1", "4:3", "3:4", "21:9", "adaptive"}
)
SEEDANCE25_MAX_PROMPT_BYTES = 9_800
MAX_WAIT_SECONDS = 30 * 60


def require_generation_consent(confirmed: bool) -> None:
    if not confirmed:
        raise LumenflowError(
            "generation_confirmation_required",
            "Seedance 2.5 generation requires --confirm-generation.",
        )


def seedance25_input(args: argparse.Namespace) -> tuple[str, int, str]:
    if args.prompt is not None:
        prompt = args.prompt
    else:
        path: Path = args.prompt_file
        try:
            if path.stat().st_size > 64 * 1024:
                raise LumenflowError(
                    "invalid_input",
                    "The prompt file exceeds the 64 KB client limit.",
                )
            prompt = path.read_text(encoding="utf-8")
        except LumenflowError:
            raise
        except (OSError, UnicodeDecodeError):
            raise LumenflowError(
                "invalid_input",
                "The prompt file must be readable UTF-8 text.",
            ) from None
    prompt = prompt.strip()
    if not prompt:
        raise LumenflowError("invalid_input", "The Seedance 2.5 prompt is required.")
    if len(prompt.encode("utf-8")) > SEEDANCE25_MAX_PROMPT_BYTES:
        raise LumenflowError(
            "invalid_input",
            "The Seedance 2.5 prompt exceeds the 9800-byte limit.",
        )
    if args.duration < 4 or args.duration > 30:
        raise LumenflowError("invalid_input", "Duration must be from 4 to 30 seconds.")
    if args.aspect_ratio not in SEEDANCE25_ASPECT_RATIOS:
        raise LumenflowError("invalid_input", "The aspect ratio is not supported.")
    return prompt, args.duration, args.aspect_ratio


def run_seedance25_workflow(
    client: LumenflowClient,
    args: argparse.Namespace,
    *,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    random_fn: Callable[[], float] = random.random,
) -> dict[str, Any]:
    require_generation_consent(args.confirm_generation)
    if args.download_dir is not None and not args.wait:
        raise LumenflowError(
            "invalid_input",
            "--download-dir requires waiting for completion.",
        )
    _validate_polling(args.poll_interval, args.max_wait)
    if len(args.reference) > 40:
        raise LumenflowError("invalid_input", "Seedance 2.5 accepts at most 40 references.")
    prompt, duration, aspect_ratio = seedance25_input(args)
    idempotency_root = validate_idempotency_key(
        args.idempotency_key
    )
    if len(idempotency_root) > 90:
        raise LumenflowError(
            "invalid_input",
            "Seedance 2.5 workflow idempotency key must be no more than 90 characters.",
        )
    uploads: list[dict[str, Any]] = []
    asset_ids: list[str] = []
    for index, reference in enumerate(args.reference, start=1):
        uploaded = client.seedance25_upload(
            reference,
            idempotency_key=f"{idempotency_root}:upload:{index}",
        )
        asset_id = _asset_id(uploaded)
        asset_ids.append(asset_id)
        uploads.append({"asset_id": asset_id, "ordinal": index})

    created = client.seedance25_create_job(
        prompt=prompt,
        duration_seconds=duration,
        aspect_ratio=aspect_ratio,
        asset_ids=asset_ids,
        idempotency_key=f"{idempotency_root}:create",
    )
    job_id = _job_id(created)
    result: dict[str, Any] = {
        "job_id": job_id,
        "uploads": uploads,
        "job": created,
        "wait_timed_out": False,
    }
    if not args.wait:
        return result

    latest, timed_out = _wait_for_job(
        client,
        job_id,
        poll_interval=args.poll_interval,
        deadline=monotonic() + args.max_wait,
        sleep=sleep,
        monotonic=monotonic,
        random_fn=random_fn,
    )
    result["job"] = latest
    result["wait_timed_out"] = timed_out
    if args.download_dir is not None and not timed_out:
        result["downloads"] = download_seedance25_assets(
            client,
            job_id,
            args.download_dir,
            overwrite=False,
            max_bytes=None,
        )
    return result


def _wait_for_job(
    client: LumenflowClient,
    job_id: str,
    *,
    poll_interval: float,
    deadline: float,
    sleep: Callable[[float], None],
    monotonic: Callable[[], float],
    random_fn: Callable[[], float],
) -> tuple[Any, bool]:
    latest: Any = {}
    while True:
        latest = client.seedance25_job(job_id)
        status = _status(latest)
        if status == "completed":
            return latest, False
        if status == "failed":
            raise LumenflowError(
                "generation_failed",
                "The Seedance 2.5 job failed.",
                request_id=client.last_request_id,
            )
        remaining = deadline - monotonic()
        if remaining <= 0:
            return latest, True
        base_delay = client.last_retry_after or poll_interval
        sleep(min(max(0.1, base_delay * (0.9 + 0.2 * random_fn())), remaining))


def _validate_polling(interval: float, max_wait: float) -> None:
    if not math.isfinite(interval) or interval < 1 or interval > 60:
        raise LumenflowError(
            "invalid_input",
            "Polling interval must be between 1 and 60 seconds.",
        )
    if not math.isfinite(max_wait) or max_wait < 1 or max_wait > MAX_WAIT_SECONDS:
        raise LumenflowError(
            "invalid_input",
            "Maximum wait must be between 1 and 1800 seconds.",
        )


def _asset_id(response: Any) -> str:
    value = response.get("asset_id") if isinstance(response, dict) else None
    if not isinstance(value, str) or not value.startswith("swa_"):
        raise LumenflowError(
            "invalid_response",
            "Lumenflow did not return a public Seedance 2.5 asset ID.",
        )
    return value


def _job_id(response: Any) -> str:
    value = response.get("job_id") if isinstance(response, dict) else None
    if not isinstance(value, str) or not value.startswith("swj_"):
        raise LumenflowError(
            "invalid_response",
            "Lumenflow did not return a public Seedance 2.5 job ID.",
        )
    return value


def _status(response: Any) -> str:
    value = response.get("status") if isinstance(response, dict) else None
    if value not in {"pending", "queued", "generating", "completed", "failed"}:
        raise LumenflowError(
            "invalid_response",
            "Lumenflow returned an unknown Seedance 2.5 job state.",
        )
    return value
