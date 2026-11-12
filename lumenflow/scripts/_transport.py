"""Bounded response reads and atomic, no-clobber local downloads."""
from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from urllib.request import Request

try:
    from ._contract import LumenflowError
except ImportError:
    from _contract import LumenflowError

MAX_DOWNLOAD_BYTES = 500_000_000
DOWNLOAD_SECONDS = 300.0
REQUEST_SECONDS = 120.0


def chunks(response, *, deadline, monotonic=time.monotonic, chunk_size=65536):
    # HTTPResponse.read1 performs at most one underlying read, unlike read(n),
    # which can keep accumulating a trickle indefinitely.
    read = getattr(response, "read1", response.read)
    while True:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError("response deadline exceeded")
        sock = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
        if sock is not None:
            current = sock.gettimeout()
            sock.settimeout(min(current, remaining) if current is not None else remaining)
        chunk = read(chunk_size)
        if monotonic() > deadline:
            raise TimeoutError("response deadline exceeded")
        if not chunk:
            return
        yield chunk


def read_bounded(response, limit, *, deadline, monotonic=time.monotonic):
    body = bytearray()
    for chunk in chunks(response, deadline=deadline, monotonic=monotonic):
        body.extend(chunk)
        if len(body) > limit:
            raise LumenflowError("invalid_response", "The public response exceeded the size limit.")
    return bytes(body)


def save_download(opener, url, destination, *, user_agent, timeout, overwrite,
                  max_bytes=None, monotonic=time.monotonic):
    limit = MAX_DOWNLOAD_BYTES if max_bytes is None else max_bytes
    if type(limit) is not int or not 1 <= limit <= MAX_DOWNLOAD_BYTES:
        raise LumenflowError("invalid_input", "Download limit must be 1–500000000 bytes.")
    # Resolve only the parent: never follow a destination symlink.
    destination = destination.expanduser()
    destination = destination.parent.resolve() / destination.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink() or (destination.exists() and not overwrite):
        raise LumenflowError("file_exists", "The destination already exists.")
    deadline = monotonic() + DOWNLOAD_SECONDS
    fd, temporary = tempfile.mkstemp(prefix=".lumenflow-", suffix=".part", dir=destination.parent)
    partial = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as output:
            request = Request(url, method="GET", headers={"User-Agent": user_agent})
            with opener.open(request, timeout=min(timeout, DOWNLOAD_SECONDS)) as response:
                length = getattr(response, "headers", {}).get("Content-Length")
                if length is not None and (not str(length).isdigit() or int(length) > limit):
                    raise LumenflowError("download_too_large", "The download exceeds the size limit.")
                total = 0
                for chunk in chunks(response, deadline=deadline, monotonic=monotonic):
                    total += len(chunk)
                    if total > limit:
                        raise LumenflowError("download_too_large", "The download exceeds the size limit.")
                    output.write(chunk)
                if length is not None and total != int(length):
                    raise LumenflowError("download_failed", "The download was incomplete.")
            output.flush()
            os.fsync(output.fileno())
        if overwrite:
            # replace replaces a raced symlink itself; it never follows it.
            os.replace(partial, destination)
        else:
            # Atomic no-clobber publication, even if the target appeared during IO.
            try:
                os.link(partial, destination)
            except FileExistsError:
                raise LumenflowError("file_exists", "The destination already exists.") from None
        return destination
    finally:
        partial.unlink(missing_ok=True)
