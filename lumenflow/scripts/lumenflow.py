#!/usr/bin/env python3
"""Deterministic CLI used by the Lumenflow Agent Skill."""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

try:
    from ._version import VERSION
    from ._client import (
        ClientConfig,
        LumenflowClient,
        LumenflowError,
        new_idempotency_key,
        validate_idempotency_key,
    )
    from ._downloads import (
        download_assets as _download_assets,
        download_seedance25_assets as _download_seedance25_assets,
    )
    from ._seedance25 import (
        require_generation_consent as _require_generation_consent,
        run_seedance25_workflow,
        seedance25_input as _seedance25_input,
    )
except ImportError:
    from _version import VERSION
    from _client import (
        ClientConfig,
        LumenflowClient,
        LumenflowError,
        new_idempotency_key,
        validate_idempotency_key,
    )
    from _downloads import (
        download_assets as _download_assets,
        download_seedance25_assets as _download_seedance25_assets,
    )
    from _seedance25 import (
        require_generation_consent as _require_generation_consent,
        run_seedance25_workflow,
        seedance25_input as _seedance25_input,
    )

ALLOWED_SCRIPT_SUFFIXES = frozenset({".txt", ".md", ".markdown"})
MAX_SCRIPT_BYTES = 10 * 1024 * 1024
MIN_SCRIPT_CHARACTERS = 10
MAX_WAIT_SECONDS = 30 * 60

READY_PROJECT_STATES = frozenset(
    {"ready", "generating", "partially_completed", "completed"}
)
FINAL_PROJECT_STATES = frozenset({"partially_completed", "completed", "failed"})
FAILED_PROJECT_STATES = frozenset({"content_rejected", "failed", "parse_failed"})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lumenflow",
        description="Use the official Lumenflow public API.",
    )
    parser.add_argument("--version", action="version", version=VERSION)
    parser.add_argument(
        "--base-url",
        help="Official HTTPS API URL ending in /open/v1; other hosts are refused.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=20.0,
        help="Per-request timeout in seconds.",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=3,
        help="Retries for transient network and service failures.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output.",
    )

    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("new-key", help="Generate an operation key offline; retain and reuse it with identical input after uncertainty.")
    commands.add_parser("auth-check", help="Verify the configured token.")
    commands.add_parser("styles", help="List approved public project styles.")

    empty = commands.add_parser("create-empty", help="Create an empty project without parsing.")
    empty.add_argument("--title", required=True)
    empty.add_argument("--idempotency-key", required=True)
    review = commands.add_parser("review-materials", help="Read one episode/scene page for script comparison; never writes or generates.")
    review.add_argument("project_id")
    review.add_argument("--episode-from", required=True, type=int)
    review.add_argument("--episode-to", required=True, type=int)
    review.add_argument("--limit", type=int, default=25)
    review.add_argument("--after", default="")
    for name in ("episodes", "scene-records", "asset-records", "failures"):
        records = commands.add_parser(name, help="Read one bounded page; pass next_cursor with --after.")
        records.add_argument("project_id")
        records.add_argument("--after", default="")
        records.add_argument("--limit", type=int, default=50)
    for name in ("episodes", "assets", "scenes", "prompts"):
        batch = commands.add_parser("batch-" + name, help="Atomic batch from a UTF-8 JSON file; no implicit generation.")
        batch.add_argument("project_id")
        batch.add_argument("--file", type=Path, required=True)
        batch.add_argument("--idempotency-key", required=True)

    create = commands.add_parser("create", help="Create a project.")
    _add_script_arguments(create)
    create.add_argument("--title", required=True)
    create.add_argument("--style-id")
    create.add_argument("--aspect-ratio")
    create.add_argument("--idempotency-key", required=True)

    parse = commands.add_parser("parse", help="Start project script parsing.")
    parse.add_argument("project_id")
    parse.add_argument("--idempotency-key", required=True)

    progress = commands.add_parser("progress", help="Read public project progress.")
    progress.add_argument("project_id")

    models = commands.add_parser("models", help="List models available to this project; no internal routing.")
    models.add_argument("project_id")
    model_quote = commands.add_parser("generation-quote", help="Quote selected model and eligible scenes without generating.")
    model_quote.add_argument("project_id")
    model_quote.add_argument("--selection-id", required=True)
    quote_scenes=model_quote.add_mutually_exclusive_group()
    quote_scenes.add_argument("--scene-id",action="append",dest="scene_ids")
    quote_scenes.add_argument("--retry-failed",action="store_true")

    generate = commands.add_parser("generate", help="Generate project scenes.")
    generate.add_argument("project_id")
    selection = generate.add_mutually_exclusive_group()
    selection.add_argument("--scene-id", action="append", dest="scene_ids")
    selection.add_argument("--retry-failed", action="store_true")
    generate.add_argument("--idempotency-key", required=True)
    generate.add_argument("--selection-id")
    generate.add_argument("--quote-fingerprint")
    generate.add_argument("--confirm-generation", action="store_true")

    download = commands.add_parser(
        "download",
        help="Download completed project assets.",
    )
    download.add_argument("project_id")
    download.add_argument("--output-dir", type=Path, required=True)
    download.add_argument("--asset-id", action="append", dest="asset_ids")
    download.add_argument("--overwrite", action="store_true")
    download.add_argument("--max-bytes", type=int)

    workflow = commands.add_parser(
        "workflow",
        help="Create and parse a project, optionally generate and download it.",
    )
    _add_script_arguments(workflow)
    workflow.add_argument("--title", required=True)
    workflow.add_argument("--style-id")
    workflow.add_argument("--aspect-ratio")
    workflow.add_argument(
        "--generate",
        action="store_true",
        help="Explicitly authorize generation for this one project.",
    )
    workflow.add_argument(
        "--no-wait-generation",
        action="store_false",
        dest="wait_generation",
        help="Submit generation without waiting for completion.",
    )
    workflow.set_defaults(wait_generation=True)
    workflow.add_argument("--download-dir", type=Path)
    workflow.add_argument("--poll-interval", type=float, default=5.0)
    workflow.add_argument(
        "--max-wait",
        type=float,
        default=float(MAX_WAIT_SECONDS),
    )
    workflow.add_argument("--idempotency-key", required=True)

    commands.add_parser(
        "seedance25-capabilities",
        help="Read the isolated Seedance 2.5 workbench contract.",
    )

    seedance_upload = commands.add_parser(
        "seedance25-upload",
        help="Upload one local Seedance 2.5 image or audio reference.",
    )
    seedance_upload.add_argument("file", type=Path)
    seedance_upload.add_argument("--idempotency-key", required=True)

    seedance_create = commands.add_parser(
        "seedance25-create",
        help="Create one isolated Seedance 2.5 video job.",
    )
    _add_seedance25_arguments(seedance_create)
    seedance_create.add_argument("--asset-id", action="append", dest="asset_ids")
    seedance_create.add_argument("--idempotency-key", required=True)
    seedance_create.add_argument(
        "--confirm-generation",
        action="store_true",
        help="Explicitly authorize this billable generation.",
    )

    seedance_status = commands.add_parser(
        "seedance25-status",
        help="Read one Seedance 2.5 job.",
    )
    seedance_status.add_argument("job_id")

    seedance_download = commands.add_parser(
        "seedance25-download",
        help="Download the completed video for one Seedance 2.5 job.",
    )
    seedance_download.add_argument("job_id")
    seedance_download.add_argument("--output-dir", type=Path, required=True)
    seedance_download.add_argument("--overwrite", action="store_true")
    seedance_download.add_argument("--max-bytes", type=int)

    seedance_workflow = commands.add_parser(
        "seedance25-workflow",
        help="Upload references, create one Seedance 2.5 job, wait, and optionally download.",
    )
    _add_seedance25_arguments(seedance_workflow)
    seedance_workflow.add_argument(
        "--reference",
        type=Path,
        action="append",
        default=[],
        help="Local image or audio file. Repeat for multiple references.",
    )
    seedance_workflow.add_argument("--idempotency-key", required=True)
    seedance_workflow.add_argument("--confirm-generation", action="store_true")
    seedance_workflow.add_argument("--no-wait", action="store_false", dest="wait")
    seedance_workflow.set_defaults(wait=True)
    seedance_workflow.add_argument("--download-dir", type=Path)
    seedance_workflow.add_argument("--poll-interval", type=float, default=5.0)
    seedance_workflow.add_argument("--max-wait", type=float, default=float(MAX_WAIT_SECONDS))
    from _canvas import add_canvas_commands
    add_canvas_commands(commands)
    return parser


def _add_script_arguments(parser: argparse.ArgumentParser) -> None:
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--script", help="Inline UTF-8 script text.")
    source.add_argument(
        "--file",
        type=Path,
        help="UTF-8 .txt, .md, or .markdown script file.",
    )


def _add_seedance25_arguments(parser: argparse.ArgumentParser) -> None:
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--prompt", help="Inline user prompt.")
    source.add_argument("--prompt-file", type=Path, help="UTF-8 prompt file.")
    parser.add_argument("--duration", type=int, required=True)
    parser.add_argument("--aspect-ratio", required=True)


def main(
    argv: Sequence[str] | None = None,
    *,
    client_factory: Callable[[ClientConfig], LumenflowClient] = LumenflowClient,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "new-key":
        _write_json({"idempotency_key": new_idempotency_key()}, pretty=args.pretty, stream=sys.stdout)
        return 0
    try:
        config = ClientConfig.from_env(
            base_url=args.base_url,
            timeout_seconds=args.timeout,
            max_retries=args.max_retries,
        )
        client = client_factory(config)
        result = execute(args, client)
        _write_json(
            {"ok": True, "command": args.command, "result": result},
            pretty=args.pretty,
            stream=sys.stdout,
        )
        return 0
    except LumenflowError as exc:
        _write_json(exc.as_dict(), pretty=args.pretty, stream=sys.stderr)
        return _exit_code(exc)
    except KeyboardInterrupt:
        error = LumenflowError(
            "cancelled",
            "The operation was cancelled.",
        )
        _write_json(error.as_dict(), pretty=args.pretty, stream=sys.stderr)
        return 130


def execute(args: argparse.Namespace, client: LumenflowClient) -> Any:
    if args.command.startswith("canvas-"):
        from _canvas import execute_canvas
        return execute_canvas(args, client)
    if args.command == "create-empty":
        return client.create_empty(_validated_title(args.title), idempotency_key=args.idempotency_key)
    if args.command == "review-materials":
        return client.review_materials(args.project_id, episode_from=args.episode_from, episode_to=args.episode_to, limit=args.limit, after=args.after)
    if args.command in {"episodes", "scene-records", "asset-records", "failures"}:
        return client.records(args.project_id, args.command, after=args.after, limit=args.limit)
    if args.command.startswith("batch-"):
        try:
            with args.file.open("rb") as stream:
                content = stream.read(1024 * 1024 + 1)
            if len(content) > 1024 * 1024:
                raise LumenflowError("invalid_input", "Batch exceeds 1 MiB.")
            payload = json.loads(content.decode("utf-8"))
        except (OSError, UnicodeError, ValueError):
            raise LumenflowError("invalid_input", "Batch file must contain valid UTF-8 JSON.") from None
        return client.batch(args.project_id, args.command.removeprefix("batch-"), payload, idempotency_key=args.idempotency_key)
    if args.command == "auth-check":
        return client.session()
    if args.command == "styles":
        return client.styles()
    if args.command == "create":
        return client.create_project(
            title=_validated_title(args.title),
            script=_script_text(args),
            style_id=args.style_id,
            aspect_ratio=args.aspect_ratio,
            idempotency_key=args.idempotency_key,
        )
    if args.command == "parse":
        return client.parse_project(
            args.project_id,
            idempotency_key=args.idempotency_key,
        )
    if args.command == "progress":
        return client.progress(args.project_id)
    if args.command == "models":
        return client.models(args.project_id)
    if args.command == "generation-quote":
        return client.generation_quote(args.project_id,args.selection_id,scene_ids=args.scene_ids,retry_failed=args.retry_failed)
    if args.command == "generate":
        if args.selection_id and not args.confirm_generation:
            raise LumenflowError("generation_confirmation_required", "Confirm the selected model and quoted cost before generating.")
        return client.generate(
            args.project_id,
            scene_ids=args.scene_ids,
            retry_failed=args.retry_failed,
            selection_id=args.selection_id,
            quote_fingerprint=args.quote_fingerprint,
            idempotency_key=args.idempotency_key,
        )
    if args.command == "download":
        return _download_assets(
            client,
            args.project_id,
            args.output_dir,
            asset_ids=args.asset_ids,
            overwrite=args.overwrite,
            max_bytes=args.max_bytes,
        )
    if args.command == "workflow":
        return run_workflow(client, args)
    if args.command == "seedance25-capabilities":
        return client.seedance25_capabilities()
    if args.command == "seedance25-upload":
        return client.seedance25_upload(
            args.file,
            idempotency_key=args.idempotency_key,
        )
    if args.command == "seedance25-create":
        _require_generation_consent(args.confirm_generation)
        prompt, duration, aspect_ratio = _seedance25_input(args)
        return client.seedance25_create_job(
            prompt=prompt,
            duration_seconds=duration,
            aspect_ratio=aspect_ratio,
            asset_ids=args.asset_ids,
            idempotency_key=args.idempotency_key,
        )
    if args.command == "seedance25-status":
        return client.seedance25_job(args.job_id)
    if args.command == "seedance25-download":
        return _download_seedance25_assets(
            client,
            args.job_id,
            args.output_dir,
            overwrite=args.overwrite,
            max_bytes=args.max_bytes,
        )
    if args.command == "seedance25-workflow":
        return run_seedance25_workflow(client, args)
    raise LumenflowError("invalid_input", "Unknown command.")


def run_workflow(
    client: LumenflowClient,
    args: argparse.Namespace,
    *,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    random_fn: Callable[[], float] = random.random,
) -> dict[str, Any]:
    if args.download_dir is not None and not args.generate:
        raise LumenflowError(
            "invalid_input",
            "--download-dir requires explicit --generate authorization.",
        )
    _validate_polling(args.poll_interval, args.max_wait)
    script = _script_text(args)
    idempotency_root = validate_idempotency_key(
        args.idempotency_key
    )
    if len(idempotency_root) > 110:
        raise LumenflowError(
            "invalid_input",
            "Workflow idempotency key must be no more than 110 characters.",
        )
    deadline = monotonic() + args.max_wait
    steps: list[dict[str, Any]] = []
    created = client.create_project(
        title=_validated_title(args.title),
        script=script,
        style_id=args.style_id,
        aspect_ratio=args.aspect_ratio,
        idempotency_key=f"{idempotency_root}:create",
    )
    project_id = _project_id(created)
    steps.append({"name": "project_created", "data": created})

    parsed = client.parse_project(
        project_id,
        idempotency_key=f"{idempotency_root}:parse",
    )
    steps.append({"name": "parse_submitted", "data": parsed})
    parse_progress, parse_timed_out = _wait_for_project(
        client,
        project_id,
        target_states=READY_PROJECT_STATES,
        poll_interval=args.poll_interval,
        deadline=deadline,
        sleep=sleep,
        monotonic=monotonic,
        random_fn=random_fn,
    )
    steps.append({"name": "parse_progress", "data": parse_progress})
    if parse_timed_out:
        return {
            "project_id": project_id,
            "wait_timed_out": True,
            "generation_submitted": False,
            "steps": steps,
        }

    if not args.generate:
        return {
            "project_id": project_id,
            "wait_timed_out": False,
            "generation_submitted": False,
            "steps": steps,
        }

    generated = client.generate(
        project_id,
        idempotency_key=f"{idempotency_root}:generate",
    )
    steps.append({"name": "generation_submitted", "data": generated})
    if not args.wait_generation:
        return {
            "project_id": project_id,
            "wait_timed_out": False,
            "generation_submitted": True,
            "steps": steps,
        }

    generation_progress, generation_timed_out = _wait_for_project(
        client,
        project_id,
        target_states=FINAL_PROJECT_STATES,
        poll_interval=args.poll_interval,
        deadline=deadline,
        sleep=sleep,
        monotonic=monotonic,
        random_fn=random_fn,
    )
    steps.append({"name": "generation_progress", "data": generation_progress})
    result: dict[str, Any] = {
        "project_id": project_id,
        "wait_timed_out": generation_timed_out,
        "generation_submitted": True,
        "steps": steps,
    }
    if args.download_dir is not None and not generation_timed_out:
        result["downloads"] = _download_assets(
            client,
            project_id,
            args.download_dir,
            asset_ids=None,
            overwrite=False,
            max_bytes=None,
        )
    return result


def _wait_for_project(
    client: LumenflowClient,
    project_id: str,
    *,
    target_states: frozenset[str],
    poll_interval: float,
    deadline: float,
    sleep: Callable[[float], None],
    monotonic: Callable[[], float],
    random_fn: Callable[[], float],
) -> tuple[Any, bool]:
    latest: Any = {}
    while True:
        latest = client.progress(project_id)
        status = _project_status(latest)
        if status in target_states:
            return latest, False
        if status in FAILED_PROJECT_STATES:
            raise LumenflowError(
                "generation_failed" if status == "failed" else status,
                "The project stopped before the requested workflow completed.",
                retriable=False,
                request_id=client.last_request_id,
            )
        remaining = deadline - monotonic()
        if remaining <= 0:
            return latest, True
        base_delay = client.last_retry_after or poll_interval
        jittered = base_delay * (0.9 + (0.2 * random_fn()))
        sleep(min(max(0.1, jittered), remaining))


def _script_text(args: argparse.Namespace) -> str:
    if getattr(args, "script", None) is not None:
        text = args.script
    else:
        path: Path = args.file
        if path.suffix.lower() not in ALLOWED_SCRIPT_SUFFIXES:
            raise LumenflowError(
                "invalid_input",
                "Only .txt, .md, and .markdown script files are supported.",
            )
        try:
            if path.stat().st_size > MAX_SCRIPT_BYTES:
                raise LumenflowError(
                    "invalid_input",
                    "The script file exceeds the 10 MB client limit.",
                )
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise LumenflowError(
                "invalid_input",
                "The script file must be UTF-8 text.",
            ) from None
        except OSError:
            raise LumenflowError(
                "invalid_input",
                "The script file could not be read.",
            ) from None
    text = text.strip()
    if len(text) < MIN_SCRIPT_CHARACTERS:
        raise LumenflowError(
            "invalid_input",
            f"The script must contain at least {MIN_SCRIPT_CHARACTERS} characters.",
        )
    return text


def _validated_title(value: str) -> str:
    value = value.strip()
    if not value or len(value) > 120:
        raise LumenflowError(
            "invalid_input",
            "Project title must be 1-120 characters.",
        )
    return value


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


def _project_id(response: Any) -> str:
    if not isinstance(response, dict):
        raise LumenflowError(
            "invalid_response",
            "Lumenflow did not return a public project ID.",
        )
    value = response.get("project_id")
    if not isinstance(value, str) or not value:
        raise LumenflowError(
            "invalid_response",
            "Lumenflow did not return a public project ID.",
        )
    return value


def _project_status(response: Any) -> str:
    if not isinstance(response, dict):
        raise LumenflowError(
            "invalid_response",
            "Lumenflow did not return a public project status.",
        )
    value = response.get("status")
    if not isinstance(value, str) or not value:
        raise LumenflowError(
            "invalid_response",
            "Lumenflow did not return a public project status.",
        )
    return value


def _write_json(
    value: Any,
    *,
    pretty: bool,
    stream: Any,
) -> None:
    json.dump(
        value,
        stream,
        ensure_ascii=False,
        indent=2 if pretty else None,
        separators=None if pretty else (",", ":"),
        sort_keys=pretty,
    )
    stream.write("\n")


def _exit_code(error: LumenflowError) -> int:
    if error.code in {
        "authentication_required",
        "token_expired",
        "token_invalid",
        "token_revoked",
    }:
        return 3
    if error.code in {
        "configuration_required",
        "forbidden_parameter",
        "generation_confirmation_required",
        "invalid_configuration",
        "invalid_input",
    }:
        return 2
    if error.code in {"rate_limited", "service_unavailable"}:
        return 4
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
