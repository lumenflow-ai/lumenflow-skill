"""Endpoint-specific public field allowlists, including nested records.

Field omission is tolerated for sparse replies; unknown fields and wrong value
types fail closed. Workflow code separately requires the IDs/states it needs.
"""
from __future__ import annotations

import math
import re
from urllib.parse import urlsplit

try:
    from ._contract import LumenflowError, validate_public_response
except ImportError:
    from _contract import LumenflowError, validate_public_response


def blocked():
    raise LumenflowError("unsafe_response_blocked", "The response did not match the public endpoint schema.")


def text(value):
    if not isinstance(value, str) or len(value.encode("utf-8")) > 4096:
        blocked()


def number(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        blocked()


def boolean(value):
    if type(value) is not bool:
        blocked()


def nullable(schema):
    return lambda value: None if value is None else check(value, schema)


def check(value, schema, depth=0):
    if depth > 12:
        blocked()
    if isinstance(schema, dict):
        if not isinstance(value, dict) or set(value) - set(schema):
            blocked()
        for key, child in value.items():
            check(child, schema[key], depth + 1)
    elif isinstance(schema, list):
        if not isinstance(value, list) or len(value) > 10000:
            blocked()
        for child in value:
            check(child, schema[0], depth + 1)
    else:
        schema(value)


ASSET = {"asset_id": text, "scene_id": text, "filename": text, "content_type": text,
         "size_bytes": number, "download_url": text}
PROJECT = {"project_id": text, "status": text, "project_url": text}
JOB = {"job_id": text, "status": text, "duration_seconds": number, "aspect_ratio": text,
       "resolution": text, "reference_counts": {"images": number, "audio": number},
       "estimated_credits": number, "failure_code": nullable(text),
       "created_at": nullable(text), "completed_at": nullable(text)}
SCENE_RECORD = {"id": text, "episode_id": nullable(text), "episode_number": nullable(number),
                "scene_number": number, "status": text}
RECORDS = {
    "episodes": {"id": text, "episode_number": number, "title": nullable(text)},
    "asset-records": {"id": text, "category": text, "name": text, "status": text},
    "scene-records": {**SCENE_RECORD, "edit_version": text},
    "failures": {**SCENE_RECORD, "error": text},
}
BATCH = {
    "episodes/batch": {"item_index": number, "episode_id": text, "episode_number": number},
    "asset-records/batch": {"item_index": number, "asset_id": text},
    "scenes/batch": {"item_index": number, "scene_id": text, "episode_id": nullable(text),
                     "scene_number": number, "edit_version": text},
    "scenes/prompts/batch": {"item_index": number, "scene_id": text, "episode_id": nullable(text),
                             "scene_number": number, "edit_version": text},
}


def validate_endpoint_response(method, path, value):
    pending = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        if depth > 12:
            blocked()
        if isinstance(item, dict):
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
    route = urlsplit(path).path
    if method == "GET" and re.fullmatch(r"/projects/[A-Za-z0-9_-]+/review-materials", route):
        try:
            from ._review import validate_review_response
        except ImportError:
            from _review import validate_review_response
        validate_review_response(value)
        return
    validate_public_response(value)
    if route == "/canvases" or route.startswith("/canvases/"):
        try:
            from ._canvas import validate_canvas_response
        except ImportError:
            from _canvas import validate_canvas_response
        validate_canvas_response(path, value)
        return
    if re.fullmatch(r"/projects/[A-Za-z0-9_-]+/(models|quotes)", route):
        try:
            from ._models import validate_models_response
        except ImportError:
            from _models import validate_models_response
        validate_models_response(route, value)
        return
    schema = None
    if method == "GET" and route == "/session":
        schema = {"authenticated": boolean, "scopes": [text], "expires_at": text}
    elif method == "GET" and route == "/styles":
        schema = {"styles": [{"style_id": text, "name": text, "description": text}],
                  "aspect_ratios": [text], "default_style_id": nullable(text), "default_aspect_ratio": text}
    elif method == "POST" and route == "/projects":
        schema = PROJECT
    elif match := re.fullmatch(r"/projects/[A-Za-z0-9_-]+/(.+)", route):
        suffix = match[1]
        if method == "POST" and suffix == "parse":
            schema = {"project_id": text, "status": text, "task_id": text}
        elif method == "POST" and suffix == "generations":
            schema = {"project_id": text, "status": text, "task_id": text,
                      "accepted_scene_ids": [text], "estimated_credits": number}
        elif method == "GET" and suffix == "progress":
            schema = {**PROJECT, "scenes": {k: number for k in ("total", "completed", "running", "pending", "failed")},
                      "scene_items": [{"scene_id": text, "index": number, "status": text, "error": nullable(text)}]}
        elif method == "GET" and suffix == "assets":
            schema = {"project_id": text, "assets": [ASSET]}
        elif method == "GET" and suffix in RECORDS:
            schema = {"project_id": text, "items": [RECORDS[suffix]], "next_cursor": nullable(text)}
        elif suffix in BATCH and method == ("PATCH" if suffix == "scenes/prompts/batch" else "POST"):
            schema = {"project_id": text, "batch_id": text, "items": [BATCH[suffix]]}
    elif route.startswith("/workbenches/seedance-2.5/"):
        suffix = route.removeprefix("/workbenches/seedance-2.5/")
        if method == "GET" and suffix == "capabilities":
            schema = {"workbench": text, "resolution": text, "duration_seconds": {"min": number, "max": number},
                      "aspect_ratios": [text], "prompt_max_bytes": number,
                      "reference_limits": {"images": number, "audio": number, "video": number,
                                           "file_size_bytes": number, "image_file_size_bytes": number,
                                           "audio_file_size_bytes": number,
                                           "audio_duration_seconds": {"min": number, "max": number, "total_max": number}},
                      "pricing": {"billing_unit": text, "credits_per_second": number}}
        elif method == "POST" and suffix == "assets":
            schema = {"asset_id": text, "media_kind": text, "content_type": text, "size_bytes": number, "created_at": nullable(text)}
        elif (method == "POST" and suffix == "jobs") or (method == "GET" and re.fullmatch(r"jobs/[A-Za-z0-9_-]+", suffix)):
            schema = JOB
        elif method == "GET" and re.fullmatch(r"jobs/[A-Za-z0-9_-]+/assets", suffix):
            schema = {"job_id": text, "assets": [{k: v for k, v in ASSET.items() if k != "scene_id"}]}
    if schema is None:
        blocked()
    check(value, schema)
