"""Closed DTO for the separately authorized review-materials endpoint only."""
from __future__ import annotations

from typing import Any

try:
    from ._contract import LumenflowError
except ImportError:
    from _contract import LumenflowError


def _blocked() -> None:
    raise LumenflowError("unsafe_response_blocked", "The review response did not match its safe public schema.")


def _object(value: Any, keys: str) -> dict:
    if not isinstance(value, dict) or set(value) != set(keys.split()):
        _blocked()
    return value


def _text(value: Any, maximum: int, nullable: bool = False) -> None:
    if nullable and value is None:
        return
    if not isinstance(value, str) or len(value.encode("utf-8")) > maximum:
        _blocked()


def _integer(value: Any) -> None:
    if type(value) is not int or value < 0:
        _blocked()


def _list(value: Any, maximum: int) -> list:
    if not isinstance(value, list) or len(value) > maximum:
        _blocked()
    return value


def validate_review_response(value: Any) -> None:
    root = _object(value, "project_id episode_from episode_to observed_at consistency missing_episode_numbers episode next_cursor")
    _text(root["project_id"], 128)
    _text(root["next_cursor"], 2048, True)
    for name in ("episode_from", "episode_to", "observed_at"):
        _integer(root[name])
    if root["consistency"] != "per_page":
        _blocked()
    if not 1 <= root["episode_from"] <= root["episode_to"] or root["episode_to"] - root["episode_from"] >= 20:
        _blocked()
    for number in _list(root["missing_episode_numbers"], 20):
        _integer(number)
        if not root["episode_from"] <= number <= root["episode_to"]:
            _blocked()
    if root["episode"] is None:
        if root["next_cursor"] is not None:
            _blocked()
        return
    ep = _object(root["episode"], "id episode_number title parse_status source scenes")
    _text(ep["id"], 128)
    _integer(ep["episode_number"])
    if not root["episode_from"] <= ep["episode_number"] <= root["episode_to"]:
        _blocked()
    _text(ep["title"], 4096, True)
    _text(ep["parse_status"], 64)
    source = _object(ep["source"], "status provenance script_content version")
    if source["status"] == "available":
        if source["provenance"] != "stored_episode_offsets":
            _blocked()
        _text(source["script_content"], 256 * 1024)
        _text(source["version"], 64)
    elif source["status"] in ("missing", "invalid_bounds", "too_large", "unsupported_workflow"):
        if any(source[key] is not None for key in ("provenance", "script_content", "version")):
            _blocked()
    else:
        _blocked()
    for item in _list(ep["scenes"], 50):
        scene = _object(item, "id scene_number status duration prompt content_status edit_version asset_references")
        _text(scene["id"], 128)
        _integer(scene["scene_number"])
        _text(scene["status"], 64)
        _text(scene["duration"], 32)
        if scene["content_status"] == "available":
            _text(scene["prompt"], 32 * 1024)
            _text(scene["edit_version"], 64)
        elif scene["content_status"] != "too_large" or scene["prompt"] is not None or scene["edit_version"] is not None:
            _blocked()
        for item in _list(scene["asset_references"], 50):
            asset = _object(item, "id name category")
            _text(asset["id"], 128)
            _text(asset["name"], 4096)
            _text(asset["category"], 64)
