"""Narrow canvas operations. Transport tickets stay inside this module."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener
from urllib.error import HTTPError, URLError

try:
    from ._contract import LumenflowError, validate_resource_id, validate_idempotency_key, validate_public_response
except ImportError:
    from _contract import LumenflowError, validate_resource_id, validate_idempotency_key, validate_public_response

UPLOAD_HOST = "upload.lumenflow.net"
UPLOAD_ORIGIN = "https://lumenflow.net"
FILE_KINDS = {".jpg":"image", ".jpeg":"image", ".png":"image", ".webp":"image",
              ".mp3":"audio", ".wav":"audio", ".m4a":"audio", ".aac":"audio",
              ".mp4":"video", ".mov":"video"}
MAX_BYTES = {"image":20*1024*1024, "audio":5*1024*1024, "video":500_000_000}


def invalid(message: str = "Invalid canvas request or response.") -> None:
    raise LumenflowError("invalid_input", message)


def obj(value: Any, allowed: set[str]) -> dict:
    if not isinstance(value, dict) or set(value) - allowed:
        invalid()
    return value


def bounded_list(value: Any, maximum: int = 50) -> list:
    if not isinstance(value, list) or len(value) > maximum:
        invalid()
    return value


def validate_canvas_response(path: str, value: Any) -> None:
    """Closed shapes; no generic arbitrary nested metadata from Web services."""
    if not isinstance(value, dict):
        invalid()
    route = urlsplit(path).path
    job_keys = {"canvas_id", "job_id", "node_id", "version_id", "status", "media_id", "credits_charged", "refunded", "error", "enhancement"}
    upload_keys = {"canvas_id", "upload_id", "node_id", "status", "version_id", "media_id", "node_revision", "transport"}
    if route == "/canvases": root = {"canvases", "canvas_id", "document_revision"}
    elif route.endswith("/content"): root = {"canvas_id", "document_revision", "nodes"}
    elif route.endswith("/models"): root = {"canvas_id", "models"}
    elif route.endswith("/quotes"): root = {"canvas_id", "total_credits", "quote_fingerprint", "expires_at"}
    elif route.endswith("/commands"): root = {"canvas_id", "document_revision", "resource_ids"}
    elif "/uploads" in route: root = upload_keys
    elif route.endswith("/download"): root = {"asset_id", "download_url", "expires_at"}
    elif "/jobs/" in route or route.endswith("/generations"): root = job_keys
    elif re.fullmatch(r"/canvases/[A-Za-z][A-Za-z0-9_-]{2,127}", route):
        root = {"canvas_id", "document_revision", "nodes", "edges", "next_node_after", "next_edge_after"}
    else: invalid()
    obj(value, root)
    if "canvases" in value:
        for row in bounded_list(value["canvases"], 100): obj(row, {"canvas_id", "name"})
    if "nodes" in value:
        for row in bounded_list(value["nodes"]):
            obj(row, {"node_id", "type", "purpose", "status", "job_id", "revision", "current_version_id"} |
                ({"text", "image_parts", "video_parts"} if route.endswith("/content") else set()))
            for field in ("image_parts", "video_parts"):
                if field in row:
                    for part in bounded_list(row[field]):
                        obj(part, {"type", "text", "node_id"})
                        if part.get("type") not in ("text", "reference"): invalid()
                        if "text" in part and (not isinstance(part["text"], str) or len(part["text"].encode()) > 32768): invalid()
            if "text" in row and (not isinstance(row["text"], str) or len(row["text"].encode()) > 32768): invalid()
    if "edges" in value:
        for row in bounded_list(value["edges"]): obj(row, {"edge_id", "source_node_id", "target_node_id", "role", "revision"})
    if "models" in value:
        for model in bounded_list(value["models"], 100):
            obj(model, {"selection_id", "name", "kind", "parameters", "pricing", "reference_limits", "delivery"})
            if "delivery" in model:
                from _models import delivery
                delivery(model["delivery"])
            if "reference_limits" in model:
                limits = obj(model["reference_limits"], {"image_min", "image_max", "audio_max", "video_max", "maximum_media", "visual_required"})
                if any(not isinstance(v, (int, bool)) for v in limits.values()): invalid()
            for field in bounded_list(model.get("parameters", [])):
                obj(field, {"field", "type", "required", "minimum", "maximum", "default", "options"})
                for key in ("default", "minimum", "maximum"):
                    if key in field and not isinstance(field[key], (str, int, float, bool)): invalid()
                if "options" in field and any(not isinstance(x, (str, int, float, bool)) for x in bounded_list(field["options"], 100)): invalid()
            if "pricing" in model:
                price = obj(model["pricing"], {"unit_credits", "unit_credits_by_resolution", "billing_unit"})
                if "unit_credits_by_resolution" in price:
                    if not isinstance(price["unit_credits_by_resolution"], dict) or any(not re.fullmatch(r"(?:[1-9]\d{2,3}p|[1248][kK])", k) for k in price["unit_credits_by_resolution"]): invalid()
    if "enhancement" in value:
        enhancement=obj(value["enhancement"], {"phase", "refunded_credits"})
        if enhancement.get("phase") not in ("enhancing", "indeterminate", "completed", "enhancement_failed"): invalid()
        from _models import credits
        credits(enhancement.get("refunded_credits"))
    if value.get("transport") is not None:
        transport = obj(value["transport"], {"upload_url", "upload_token", "expires_at"})
        if not path.endswith("/uploads"): invalid()
        validate_upload_url(transport.get("upload_url", ""))
        if not isinstance(transport.get("upload_token"), str) or len(transport["upload_token"]) > 16384: invalid()
    # Reject arbitrary objects hidden in scalar fields.
    for key, child in value.items():
        if key not in {"nodes", "edges", "models", "canvases", "transport", "resource_ids", "enhancement", "delivery"} and isinstance(child, (dict, list)): invalid()
    if "resource_ids" in value:
        for identity in bounded_list(value["resource_ids"], 20): validate_resource_id(identity, "resource")
    containers = {"canvases", "nodes", "edges", "models", "parameters", "pricing", "unit_credits_by_resolution",
                  "image_parts", "video_parts", "options", "reference_limits", "transport", "resource_ids", "enhancement", "delivery"}
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            for key, child in item.items():
                if isinstance(child, (dict, list)):
                    if key not in containers: invalid()
                    pending.append(child)
        elif isinstance(item, list):
            pending.extend(item)
    validate_public_response(value)


def validate_upload_url(url: str) -> None:
    p = urlsplit(url)
    if (p.scheme != "https" or p.hostname != UPLOAD_HOST or p.port not in (None,443)
        or p.username or p.password or p.query or p.fragment or p.path != "/upload/canvas-media"):
        invalid("The canvas upload host is not approved.")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        raise URLError("canvas upload redirect refused")


class CanvasClient:
    def __init__(self, client: Any, *, upload_opener: Any = None):
        self.client = client
        self.upload_opener = upload_opener or build_opener(NoRedirect())

    def request(self, method: str, path: str, payload: dict | None = None, key: str | None = None) -> Any:
        if key is not None: validate_idempotency_key(key)
        data = self.client._request(method, path, payload=payload, idempotency_key=key)
        validate_canvas_response(path, data)
        return data

    @staticmethod
    def path(canvas: str, suffix: str = "") -> str:
        return f"/canvases/{validate_resource_id(canvas, 'canvas')}{suffix}"

    def upload(self, canvas: str, node: str, revision: int, file: Path, key: str, *, create_node: bool = False, document_revision: int | None = None) -> Any:
        validate_idempotency_key(key)
        validate_resource_id(node, "node")
        try:
            file = file.expanduser().resolve(strict=True)
            size = file.stat().st_size
            kind = FILE_KINDS.get(file.suffix.lower())
            if not file.is_file() or kind is None or size < 1 or size > MAX_BYTES[kind]: invalid("The local canvas file is unsupported or too large.")
        except OSError:
            invalid("The local canvas file could not be read.")
        response = self.request("POST", self.path(canvas, "/uploads"), {"node_id":node, "kind":kind,
            "expected_bytes":size, "expected_node_revision":revision,
            **({"create_node":True,"expected_document_revision":document_revision} if create_node else {})}, key)
        transport = response.get("transport")
        upload_id = validate_resource_id(response.get("upload_id", ""), "upload")
        if response.get("status") == "succeeded":
            return {k:v for k,v in response.items() if k != "transport"}
        if not transport: invalid("The upload ticket is unavailable; query upload status before retrying.")
        validate_upload_url(transport["upload_url"])
        try:
            with file.open("rb") as body:
                request = Request(transport["upload_url"], data=body, method="POST", headers={
                    "Authorization":f"Bearer {transport['upload_token']}", "Content-Type":"application/octet-stream",
                    "Content-Length":str(size), "Origin":UPLOAD_ORIGIN})
                with self.upload_opener.open(request, timeout=900) as result:
                    result.read(65536)  # Discard Worker internals; API status is authoritative.
        except HTTPError as error:
            # Only documented media-validation failures are safe to classify.
            # Never echo Worker bodies, headers, URLs or arbitrary error strings.
            try:
                raw_error = error.read(4097)
                detail = json.loads(raw_error) if len(raw_error) <= 4096 else None
            except (ValueError, OSError):
                detail = None
            finally:
                error.close()
            validation_errors = {
                (422, "image_dimensions_invalid"): "Image width and height must each be 300-6000 pixels, with aspect ratio 0.4-2.5.",
                (413, "media_too_large"): "The media exceeds the canvas upload size limit.",
                (415, "unsupported_media"): "The media format is unsupported.",
                (415, "media_corrupt"): "The media could not be decoded safely.",
            }
            code = detail.get("error") if isinstance(detail, dict) and set(detail) == {"error"} else None
            message = validation_errors.get((error.code, code)) if isinstance(code, str) else None
            if message:
                raise LumenflowError("upload_rejected", f"{message} Query canvas-upload-status for {upload_id} before a corrected upload.") from None
            raise LumenflowError("upload_outcome_unknown", f"Upload outcome unknown. Query canvas-upload-status for {upload_id}; keep the original key.") from None
        except (URLError, OSError):
            raise LumenflowError("upload_outcome_unknown", f"Upload outcome unknown. Query canvas-upload-status for {upload_id}; keep the original key.") from None
        result = self.request("POST", self.path(canvas, f"/uploads/{upload_id}/finalize"), {})
        return {k:v for k,v in result.items() if k != "transport"}

    def download(self, canvas: str, media: str, destination: Path) -> Any:
        media = validate_resource_id(media, "media")
        result = self.request("POST", self.path(canvas, f"/results/{media}/download"), {})
        path = result.get("download_url", "")
        if not re.fullmatch(r"/open/v1/canvas-downloads/[A-Za-z0-9_-]{43}", path): invalid()
        base = urlsplit(self.client.config.base_url)
        url = urlunsplit((base.scheme, base.netloc, path, "", ""))
        saved = self.client.download(url, destination, max_bytes=500_000_000)
        return {"media_id":media, "path":str(saved)}


def add_canvas_commands(commands: Any) -> None:
    commands.add_parser("canvas-list", help="List only explicitly selected accessible canvases.")
    create = commands.add_parser("canvas-create", help="Create own canvas; does not expand Token range.")
    create.add_argument("--name", required=True); create.add_argument("--idempotency-key", required=True)
    for name in ("read", "content", "edit", "models", "quote", "generate", "status", "retry", "upload", "upload-status", "download"):
        parser = commands.add_parser(f"canvas-{name}")
        parser.add_argument("canvas_id")
        if name in ("content",): parser.add_argument("--node-id", action="append", required=True)
        if name in ("edit", "quote", "generate", "retry"): parser.add_argument("--file", type=Path, required=True)
        if name in ("edit", "generate", "retry", "upload"): parser.add_argument("--idempotency-key", required=True)
        if name in ("generate", "retry"): parser.add_argument("--confirm-generation", action="store_true")
        if name in ("status", "retry"): parser.add_argument("job_id")
        if name == "upload-status": parser.add_argument("upload_id")
        if name == "read":
            parser.add_argument("--node-after", default="");parser.add_argument("--edge-after", default="");parser.add_argument("--limit", type=int, default=50)
        if name == "upload":
            parser.add_argument("node_id");parser.add_argument("--revision", type=int, required=True);parser.add_argument("--file", type=Path, required=True)
            parser.add_argument("--create-node", action="store_true");parser.add_argument("--document-revision", type=int)
        if name == "download": parser.add_argument("media_id");parser.add_argument("--output", type=Path, required=True)


def execute_canvas(args: Any, client: Any) -> Any:
    from urllib.parse import urlencode
    canvas = CanvasClient(client)
    name = args.command.removeprefix("canvas-")
    if name == "list": return canvas.request("GET", "/canvases")
    if name == "create": return canvas.request("POST", "/canvases", {"name":args.name}, args.idempotency_key)
    path = canvas.path(args.canvas_id)
    if name == "read": return canvas.request("GET", path + "?" + urlencode({"limit":args.limit,
        **({"node_after":args.node_after} if args.node_after else {}), **({"edge_after":args.edge_after} if args.edge_after else {})}))
    if name == "content": return canvas.request("POST", path+"/content", {"node_ids":args.node_id})
    if name == "models": return canvas.request("GET", path+"/models")
    if name == "status": return canvas.request("GET", path+f"/jobs/{validate_resource_id(args.job_id,'job')}")
    if name == "upload-status": return canvas.request("GET", path+f"/uploads/{validate_resource_id(args.upload_id,'upload')}")
    if name == "upload": return canvas.upload(args.canvas_id,args.node_id,args.revision,args.file,args.idempotency_key,create_node=args.create_node,document_revision=args.document_revision)
    if name == "download": return canvas.download(args.canvas_id,args.media_id,args.output)
    if name in ("generate","retry") and not args.confirm_generation:
        invalid("Explicit authorization for this billable canvas generation is required.")
    try:
        with args.file.open("rb") as source: raw=source.read(1024*1024+1)
        if len(raw)>1024*1024: invalid("Canvas JSON exceeds 1 MiB.")
        payload=json.loads(raw.decode("utf-8"))
        if not isinstance(payload,dict): invalid()
    except (OSError,UnicodeError,ValueError): invalid("Canvas input must be a UTF-8 JSON object.")
    suffix = {"edit":"/commands", "quote":"/quotes", "generate":"/generations"}.get(name)
    if name == "retry": suffix=f"/jobs/{validate_resource_id(args.job_id,'job')}/retry"
    if suffix is None: invalid()
    return canvas.request("POST", path+suffix, payload, getattr(args,"idempotency_key",None))
