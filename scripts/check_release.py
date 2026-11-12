#!/usr/bin/env python3
"""Offline release checks. Findings never include matched secret values."""
from __future__ import annotations

import ast
import json
import os
import re
import socket
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def has_git_metadata():
    # Never inherit Git history from a parent directory of an extracted archive.
    return (ROOT / ".git").exists()


def source_files():
    excluded_dirs = {".git", ".venv", "__pycache__", "dist", "out"}
    return {str(p.relative_to(ROOT)) for p in ROOT.rglob("*")
            if not any(part in excluded_dirs for part in p.relative_to(ROOT).parts)
            and (p.is_file() or p.is_symlink())
            and p.name != ".DS_Store" and p.suffix not in {".pyc", ".pyo"}}
RULES = {
    "private-key": rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----",
    "github-token": rb"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})",
    "aws-key": rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b",
    "provider-key": rb"\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{24,}",
    "lumenflow-token": rb"\blf_[A-Za-z0-9_-]{24,}",
    "jwt": rb"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
    "slack-token": rb"\bxox[baprs]-[A-Za-z0-9-]{20,}",
}


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def scan(data, label):
    return [{"rule": name, "file": label, "line": data[:m.start()].count(b"\n") + 1}
            for name, pattern in RULES.items() for m in re.finditer(pattern, data)]


def scan_history_and_worktree():
    findings = []
    blobs = 0
    if has_git_metadata():
        for entry in git("rev-list", "--objects", "--all").decode().splitlines():
            sha, _, path = entry.partition(" ")
            if git("cat-file", "-t", sha).strip() != b"blob":
                continue
            blobs += 1
            findings.extend(scan(git("cat-file", "blob", sha), f"history:{sha[:12]}:{path}"))
        files = set(git("ls-files", "-z", "--cached", "--others", "--exclude-standard").decode().split("\0")) - {""}
    else:
        files = source_files()
    for name in sorted(files):
        path = ROOT / name
        if path.is_symlink():
            findings.append({"rule": "unexpected-symlink", "file": name})
            continue
        if not path.is_file():
            continue
        if path.name.startswith(".env") or path.suffix in {".pem", ".key", ".p12", ".db", ".sqlite"}:
            findings.append({"rule": "sensitive-file", "file": name})
        findings.extend(scan(path.read_bytes(), f"worktree:{name}"))
    print(json.dumps({"history_blobs": blobs, "worktree_files": len(files), "findings_redacted": findings}, indent=2))
    return not findings


def validate_sources():
    for path in (ROOT / "lumenflow/scripts").glob("*.py"):
        ast.parse(path.read_text(), filename=str(path), feature_version=(3, 11))
    text = (ROOT / "lumenflow/SKILL.md").read_text()
    if not text.startswith("---\nname: lumenflow\ndescription: ") or "\n---\n" not in text[4:]:
        raise ValueError("Invalid Skill frontmatter")
    for path in [ROOT / "README.md", *ROOT.glob("lumenflow/**/*.md"), *ROOT.glob("docs/*.md")]:
        for target in re.findall(r"\]\(([^)]+)\)", path.read_text()):
            if "://" not in target and not target.startswith("#") and not (path.parent / target.split("#")[0]).is_file():
                raise ValueError(f"Broken local reference in {path.name}")
    if has_git_metadata():
        git("diff", "--check")
    else:
        for name in source_files():
            path = ROOT / name
            if path.is_symlink():
                continue  # Reported by the source scan without following the link.
            try:
                lines = path.read_text().splitlines()
            except UnicodeDecodeError:
                continue
            if any(line.rstrip(" \t") != line for line in lines):
                raise ValueError(f"Trailing whitespace in {name}")


def denied(*args, **kwargs):
    raise RuntimeError("Network access is disabled during release tests")


def main():
    os.chdir(ROOT)
    validate_sources()
    clean = scan_history_and_worktree()
    with patch.dict(os.environ, {}, clear=True), patch.object(socket.socket, "connect", denied), \
         patch.object(socket.socket, "connect_ex", denied), patch.object(socket, "create_connection", denied), \
         patch.object(socket, "getaddrinfo", denied):
        result = unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.discover(str(ROOT / "tests")))
    return 0 if clean and result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
