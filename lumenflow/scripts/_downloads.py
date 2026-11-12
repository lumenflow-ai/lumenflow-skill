"""Safe result download orchestration for the Lumenflow Skill."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

try:
    from ._client import LumenflowClient, LumenflowError
except ImportError:
    from _client import LumenflowClient, LumenflowError

SAFE_FILENAME_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")


def download_assets(
    client: LumenflowClient,
    project_id: str,
    output_dir: Path,
    *,
    asset_ids: list[str] | None,
    overwrite: bool,
    max_bytes: int | None,
) -> dict[str, Any]:
    response = client.assets(project_id)
    result = _download_asset_response(
        client,
        response,
        output_dir,
        asset_ids=asset_ids,
        overwrite=overwrite,
        max_bytes=max_bytes,
    )
    return {"project_id": project_id, **result}


def download_seedance25_assets(
    client: LumenflowClient,
    job_id: str,
    output_dir: Path,
    *,
    overwrite: bool,
    max_bytes: int | None,
) -> dict[str, Any]:
    response = client.seedance25_assets(job_id)
    result = _download_asset_response(
        client,
        response,
        output_dir,
        asset_ids=None,
        overwrite=overwrite,
        max_bytes=max_bytes,
    )
    return {"job_id": job_id, **result}


def _download_asset_response(
    client: LumenflowClient,
    response: Any,
    output_dir: Path,
    *,
    asset_ids: list[str] | None,
    overwrite: bool,
    max_bytes: int | None,
) -> dict[str, Any]:
    assets = response.get("assets") if isinstance(response, dict) else None
    if not isinstance(assets, list):
        raise LumenflowError(
            "invalid_response",
            "Lumenflow did not return a valid public asset list.",
        )
    selected = set(asset_ids or [])
    unknown = set(selected)
    downloads: list[dict[str, str]] = []
    used_names: set[str] = set()
    for index, asset in enumerate(assets, start=1):
        if not isinstance(asset, dict):
            raise LumenflowError(
                "invalid_response",
                "Lumenflow returned an invalid public asset.",
            )
        asset_id = str(asset.get("asset_id") or "")
        if selected and asset_id not in selected:
            continue
        unknown.discard(asset_id)
        url = asset.get("download_url")
        if not asset_id or not isinstance(url, str):
            raise LumenflowError(
                "invalid_response",
                "A public asset is missing its ID or download URL.",
            )
        filename = _safe_filename(
            str(asset.get("filename") or f"{asset_id or index}.mp4")
        )
        filename = _deduplicate_filename(filename, used_names)
        destination = output_dir / filename
        saved = client.download(
            url,
            destination,
            overwrite=overwrite,
            max_bytes=max_bytes,
        )
        downloads.append(
            {
                "asset_id": asset_id,
                "filename": saved.name,
                "path": str(saved),
            }
        )
    if unknown:
        raise LumenflowError(
            "asset_not_found",
            "One or more requested assets were not found.",
            status=404,
        )
    return {"count": len(downloads), "files": downloads}


def _safe_filename(value: str) -> str:
    name = Path(value).name
    name = SAFE_FILENAME_PATTERN.sub("_", name).strip("._")
    if not name:
        return "result.mp4"
    return name[:180]


def _deduplicate_filename(name: str, used: set[str]) -> str:
    candidate = name
    suffix = Path(name).suffix
    stem = Path(name).stem
    counter = 2
    while candidate in used:
        candidate = f"{stem}-{counter}{suffix}"
        counter += 1
    used.add(candidate)
    return candidate
