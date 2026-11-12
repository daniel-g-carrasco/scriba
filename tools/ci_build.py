#!/usr/bin/env python3
"""Conservative packaging decisions and dependency-only Flatpak cache keys.

Tests run for every event. Only packaging may skip a proven documentation-only
change. Missing event data, history or an unfamiliar event means a full build.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

SHA = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})\Z")


def docs_only(paths):
    if not paths:
        return False
    for path in paths:
        if (not isinstance(path, str) or "\\" in path or "\0" in path
                or any(part in ("", ".", "..") for part in path.split("/"))):
            return False
        if path.lower() == "docs/licensing.md":
            return False
        if path not in ("README.md", "CONTRIBUTING.md") and not path.startswith("docs/"):
            return False
    return True


def git_binary():
    if os.name == "nt":
        # Match actions/checkout and its system configuration. MSYS Git can be
        # first on PATH but uses different defaults for the same CRLF checkout.
        candidate = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/cmd/git.exe"
        if candidate.is_file():
            return str(candidate)
    return shutil.which("git") or "git"  # Fail closed if no executable exists.


def packaging_needed(event_name, ref, event, head, root):
    if event_name == "workflow_dispatch" or ref.startswith("refs/tags/"):
        return True, "tag or manual run"
    if event_name not in ("push", "pull_request"):
        return True, "unrecognised event"
    try:
        git = git_binary()
        if event_name == "push":
            if not ref.startswith("refs/heads/"):
                return True, "unknown push reference"
            base = event["before"]
        else:
            base = event["pull_request"]["base"]["sha"]
        if (not isinstance(base, str) or not isinstance(head, str) or not SHA.fullmatch(base)
                or not SHA.fullmatch(head) or set(base) == {"0"}):
            return True, "missing or invalid comparison commit"
        checkout = subprocess.run([git, "-C", str(root), "rev-parse", "--verify", "HEAD"],
                                  check=True, capture_output=True, timeout=30).stdout.strip().decode("ascii")
        if checkout.lower() != head.lower():
            return True, "checkout differs from the event commit"
        clean = subprocess.run([git, "-C", str(root), "diff", "--no-ext-diff", "--no-textconv",
                                "--quiet", "HEAD", "--"], check=False, capture_output=True, timeout=30)
        if clean.returncode:
            return True, "tracked checkout files changed or cannot be checked"
        for commit in (base, head):
            # Native Windows callers of MSYS Git undergo argument globbing:
            # SHA^{commit} loses its braces. A validated literal SHA and its
            # object type provide the same check without revision syntax.
            kind = subprocess.run([git, "-C", str(root), "cat-file", "-t", commit],
                                  check=True, capture_output=True, timeout=30).stdout.strip()
            if kind != b"commit":
                return True, "comparison object is not a commit"
        done = subprocess.run([git, "-C", str(root), "diff", "--no-ext-diff", "--no-textconv",
                               "--no-renames", "--name-only", "-z", base, head, "--"],
                              check=True, capture_output=True, timeout=30)
        paths = [os.fsdecode(path) for path in done.stdout.split(b"\0") if path]
        safe = docs_only(paths)
        return not safe, "safe documentation only" if safe else "package inputs changed or no changes known"
    except (KeyError, TypeError, ValueError, OSError, subprocess.SubprocessError):
        return True, "comparison unavailable"


def _file_hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _local_sources(value, root, source_base, checksums):
    if isinstance(value, dict):
        if value.get("type") == "dir":
            raise ValueError("local dependency directories need an explicit cache-key policy")
        if value.get("type") in ("file", "archive", "patch") and "path" in value:
            raw = Path(value["path"])
            path = (source_base / raw).resolve()
            if root not in path.parents or not path.is_file():
                raise ValueError("local dependency source is missing or outside the checkout")
            relative = path.relative_to(root).as_posix()
            checksums[relative] = _file_hash(path)
            value["path"] = relative
        for child in value.values():
            _local_sources(child, root, source_base, checksums)
    elif isinstance(value, list):
        for child in value:
            _local_sources(child, root, source_base, checksums)


def flatpak_key(manifest, root, *, sdk_commit, runtime_commit, arch, builder_version, app_module="scriba", source_base=None):
    for commit in (sdk_commit, runtime_commit):
        if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", commit):
            raise ValueError("Flatpak cache needs the exact SDK and Platform commits")
    if not re.fullmatch(r"[a-z0-9_]+", arch) or not builder_version.strip():
        raise ValueError("invalid build architecture or builder version")
    modules = manifest.get("modules", [])
    if not modules or not all(isinstance(module, dict) for module in modules):
        raise ValueError("use flatpak-builder --show-manifest to expand the manifest first")
    names = [module.get("name") for module in modules]
    if names.count(app_module) != 1 or names[-1] != app_module:
        raise ValueError("the application must be the final named module")
    for key in ("runtime", "runtime-version", "sdk"):
        if not isinstance(manifest.get(key), str) or not manifest[key]:
            raise ValueError("manifest does not identify its runtime and SDK")
    root = Path(root).resolve()
    source_base = root if source_base is None else (root / source_base).resolve()
    if source_base != root and root not in source_base.parents:
        raise ValueError("manifest source directory is outside the checkout")
    dependencies = copy.deepcopy(modules[:-1])
    checksums = {}
    _local_sources(dependencies, root, source_base, checksums)
    payload = {key: manifest[key] for key in (
        "app-id", "id", "runtime", "runtime-version", "sdk", "sdk-extensions", "build-options",
        "base", "base-version", "base-extensions", "inherit-extensions", "inherit-sdk-extensions") if key in manifest}
    payload.update(schema=1, modules=dependencies, local_sources=checksums, arch=arch,
                   sdk_commit=sdk_commit.lower(), runtime_commit=runtime_commit.lower(), builder=builder_version)
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return f"scriba-flatpak-v1-{arch}-{digest}"


def emit(name, value, output):
    line = f"{name}={value}\n"
    if output:
        with Path(output).open("a", encoding="utf-8") as stream:
            stream.write(line)
    print(line.rstrip(), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    scope = commands.add_parser("scope")
    scope.add_argument("--root", type=Path, default=Path.cwd())
    scope.add_argument("--event-name", default=os.environ.get("GITHUB_EVENT_NAME", ""))
    scope.add_argument("--ref", default=os.environ.get("GITHUB_REF", ""))
    scope.add_argument("--head", default=os.environ.get("GITHUB_SHA", ""))
    scope.add_argument("--event-path", default=os.environ.get("GITHUB_EVENT_PATH", ""))
    scope.add_argument("--output", default=os.environ.get("GITHUB_OUTPUT", ""))
    cache = commands.add_parser("flatpak-key")
    cache.add_argument("--manifest", type=Path, required=True, help="expanded JSON from --show-manifest")
    cache.add_argument("--root", type=Path, default=Path.cwd())
    cache.add_argument("--source-base", type=Path, help="directory of the original manifest for local dependency files")
    cache.add_argument("--sdk-commit", required=True)
    cache.add_argument("--runtime-commit", required=True)
    cache.add_argument("--arch", required=True)
    cache.add_argument("--builder-version", required=True)
    cache.add_argument("--output", default=os.environ.get("GITHUB_OUTPUT", ""))
    args = parser.parse_args(argv)
    if args.command == "scope":
        try:
            event = json.loads(Path(args.event_path).read_text(encoding="utf-8")) if args.event_path else {}
        except (OSError, ValueError):
            event = {}
        needed, reason = packaging_needed(args.event_name, args.ref, event, args.head, args.root)
        emit("package", "true" if needed else "false", args.output)
        print(reason, flush=True)
    else:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        key = flatpak_key(manifest, args.root, sdk_commit=args.sdk_commit, runtime_commit=args.runtime_commit,
                          arch=args.arch, builder_version=args.builder_version, source_base=args.source_base)
        emit("key", key, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
