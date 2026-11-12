"""Resource-scoped model discovery and bounded public quote responses."""
from __future__ import annotations
import math
import re
from typing import Any
try:
    from ._contract import LumenflowError, validate_resource_id
except ImportError:
    from _contract import LumenflowError, validate_resource_id


def invalid() -> None:
    raise LumenflowError("unsafe_response", "The model response did not match the public contract.")


def record(value: Any, allowed: set[str]) -> dict:
    if not isinstance(value, dict) or set(value) - allowed: invalid()
    return value


def credits(value: Any) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0: invalid()


def delivery(value: Any) -> None:
    row=record(value, {"generation_resolution", "delivery_resolution", "enhancement_included"})
    if set(row)!={"generation_resolution", "delivery_resolution", "enhancement_included"}: invalid()
    if any(not isinstance(row[k], str) or not re.fullmatch(r"(?:\d{3,4}p|[1248][kK])", row[k]) for k in ("generation_resolution", "delivery_resolution")): invalid()
    if not isinstance(row["enhancement_included"], bool): invalid()


def validate_models_response(path: str, value: Any) -> None:
    if path.endswith("/models"):
        row=record(value,{"project_id", "models"})
        if not isinstance(row.get("models"), list) or len(row["models"])>100: invalid()
        for item in row["models"]:
            model=record(item,{"selection_id", "name", "kind", "duration", "pricing", "delivery"})
            if not isinstance(model.get("selection_id"),str) or not re.fullmatch(r"[a-f0-9]{64}",model["selection_id"]): invalid()
            if model.get("kind")!="video" or not isinstance(model.get("name"),str): invalid()
            duration=record(model.get("duration"),{"min", "max"})
            for number in duration.values(): credits(number)
            price=record(model.get("pricing"),{"billing_unit", "unit_credits"})
            if price.get("billing_unit") not in ("per_second", "per_scene", "per_request"): invalid()
            credits(price.get("unit_credits"));delivery(model.get("delivery"))
    else:
        row=record(value,{"project_id", "selection_id", "total_credits", "expires_at", "quote_fingerprint", "scene_costs", "delivery"})
        credits(row.get("total_credits"))
        if not isinstance(row.get("selection_id"),str) or not re.fullmatch(r"[a-f0-9]{64}",row["selection_id"]): invalid()
        if not isinstance(row.get("quote_fingerprint"),str) or not re.fullmatch(r"spq1\.\d{10}\.[a-f0-9]{64}",row["quote_fingerprint"]): invalid()
        if isinstance(row.get("expires_at"), bool) or not isinstance(row.get("expires_at"),int): invalid()
        if not isinstance(row.get("scene_costs"),list) or len(row["scene_costs"])>100: invalid()
        for cost in row["scene_costs"]:
            record(cost,{"scene_id","credits"});validate_resource_id(cost.get("scene_id"),"scene");credits(cost.get("credits"))
        delivery(row.get("delivery"))
    validate_resource_id(row.get("project_id"),"project")
