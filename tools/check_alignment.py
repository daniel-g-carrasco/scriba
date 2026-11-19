#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only comparison of an application's fingerprints with an approved model commit."""

from __future__ import annotations

import argparse
import datetime
import importlib.util
import io
import json
import re
import subprocess
import tarfile
import tempfile
from pathlib import Path


def git(model: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "-C", str(model), *args], capture_output=True)
    if result.returncode:
        raise ValueError(result.stderr.decode("utf-8", errors="replace").strip())
    return result.stdout


def approved_snapshot(model: Path, revision: str, dest: Path):
    if not re.fullmatch(r"[0-9a-fA-F]{7,64}", revision):
        raise ValueError("use the approved commit SHA as --revision")
    sha = git(model, "rev-parse", "--verify", f"{revision}^{{commit}}").decode().strip()
    data = git(model, "archive", "--format=tar", sha, "comune", "gtk/comune", "tools/impronte.py")
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
        archive.extractall(dest, filter="data")
    spec = importlib.util.spec_from_file_location("approved_impronte", dest / "tools" / "impronte.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    return sha, tool


def read_json(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default


def compare(tool, source: Path, target: Path, model: Path, sha: str) -> int:
    package = tool.package_of(target)
    expected = {
        "comune": tool.registry_mappings(source, package, [], [], True),
        "gtk": tool.gtk_mappings(source, package),
    }
    variants = read_json(target / "impronte" / "locali.json", {})
    drift = False
    for group, required in expected.items():
        manifest_path = target / "impronte" / f"{group}.json"
        manifest = read_json(manifest_path, tool.new_manifest(group))
        if manifest.get("group", "comune") != group:
            raise ValueError(f"{manifest_path}: wrong group")
        if not manifest_path.is_file() or not manifest["files"]:
            print(f"{target.name} {group} missing-manifest {manifest_path}")
            drift = True
        known = {(item["source"], item["target"]) for item in manifest["files"] if item.get("sha256")}
        missing = set(required) - known
        for upstream, local in sorted(missing):
            print(f"{target.name} {group} missing-component {local} ({upstream})")
            drift = True
        planned = tool.add_mappings(manifest, required)
        for row in tool.plan_updates(source, target, planned):
            state = row["status"]
            reason = variants.get(row["target"], "")
            if state == "unchanged":
                continue
            if state == "locally-modified" and isinstance(reason, str) and reason.strip():
                print(f"{target.name} {group} declared-variant {row['target']} ({reason})")
                continue
            drift = True
            print(f"{target.name} {group} {state} {row['target']} "
                  f"(base {row['baseline_version'] or 'none'}, approved {sha})")
            base = row["baseline_version"]
            if base and re.fullmatch(r"[0-9a-fA-F]{7,64}", base):
                print(f'  git -C "{model}" diff {base} {sha} -- "{row["source"]}"')
            else:
                print(f'  git -C "{model}" show "{sha}:{row["source"]}"')
    print(f"{target.name}: {'drift' if drift else 'current'}")
    return int(drift)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, type=Path, help="path to the read-only model checkout")
    parser.add_argument("--revision", required=True, help="approved model commit SHA")
    parser.add_argument("--app", type=Path, action="append", help="application root (default: the current directory)")
    args = parser.parse_args(argv)
    applications = [path.resolve() for path in args.app or [Path.cwd()]]
    model = args.model.resolve()
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    print(f"Alignment check {now}")
    with tempfile.TemporaryDirectory(prefix="gtk-alignment-") as temp:
        source = Path(temp)
        try:
            sha, tool = approved_snapshot(model, args.revision, source)
        except (OSError, ValueError, tarfile.TarError) as exc:
            for app in applications:
                print(f"{app.name}: not-checked ({exc})")
            return 2
        print(f"Approved model: {sha}")
        status = 0
        for app in applications:
            try:
                status = max(status, compare(tool, source, app, model, sha))
            except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
                print(f"{app.name}: not-checked ({exc})")
                status = 2
    return status


if __name__ == "__main__":
    raise SystemExit(main())
