"""Verify the narrowly scoped GstPlay backport receipt; never execute build inputs."""
from __future__ import annotations

import hashlib
import json
import re
import tarfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
SOURCE_SHA256 = {
    "1.26.11": "110fb82795f0e569b1e27b12ab9699d35c7762e1ff4db95335d6ac8d1442af3d",
    "1.28.2": "6467e3964828f4d7d08bfe1fbb4d76287a1c8fa76674e59e101a149c020fefd7",
    "1.28.8": "670d888bde8242579b0d8e91f60cf337aa7be20ca7c315ebfbdd6e5a9218d104",
    "1.28.9": "4a4e0b7bf84f868378c48f143a7ac3a98923b488a5b74f1a35ba46bc3c43f6b1",
}
PATCH_COMMIT = "b8b17c948de515f72e91552e1f8607d370e8ba84"
PATCH_URL = f"https://github.com/GStreamer/gstreamer/commit/{PATCH_COMMIT}.patch"
PATCH_SHA256 = "b61db8a90b08d527c10bb239c2bd5ba571d9fe57ee6e289e3cbf4d7f50217fb4"
PATCH_PROJECT_FILE = "build-aux/gstplay/gstplay-dispose.patch"
RECIPE_PROJECT_FILE = "tools/build_gstplay.py"


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def source_url(version):
    return f"https://gstreamer.freedesktop.org/src/gst-plugins-bad/gst-plugins-bad-{version}.tar.xz"


def relative_file(root, value):
    if (not isinstance(value, str) or not value or "\\" in value or ":" in value
            or "\0" in value or any(p in ("", ".", "..") for p in value.split("/"))
            or PurePosixPath(value).is_absolute()):
        raise ValueError("unsafe GstPlay receipt path")
    root = Path(root).resolve()
    path = root / value
    if not path.resolve().is_relative_to(root):
        raise ValueError("GstPlay receipt path leaves its root")
    return path


def checked(root, item, key="file"):
    digest = item.get("sha256", "")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("GstPlay input needs SHA-256")
    path = relative_file(root, item[key])
    if not path.is_file() or sha256(path) != digest:
        raise ValueError(f"GstPlay hash mismatch: {item[key]}")
    return path


def _read_receipt(path, *, output_root=None, project_root=ROOT):
    """Inputs are beside BUILD.json; outputs are relative to the supplied build root."""
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    allowed = {"schema_version", "component", "version", "source", "patch", "recipe",
               "outputs", "licence_files", "cache_key"}
    if (set(data) - allowed or data.get("schema_version") != 1 or data.get("component") != "gstplay"
            or not isinstance(data.get("version"), str) or data["version"] not in SOURCE_SHA256):
        raise ValueError("unsupported GstPlay receipt")
    version = data["version"]
    source, patch, recipe = data["source"], data["patch"], data["recipe"]
    if not all(isinstance(value, dict) for value in (source, patch, recipe)):
        raise ValueError("invalid GstPlay receipt records")
    if source != {"file": f"gst-plugins-bad-{version}.tar.xz", "url": source_url(version),
                  "sha256": SOURCE_SHA256[version]}:
        raise ValueError("GstPlay source differs from its official pin")
    if (patch.get("file") != "gstplay-dispose.patch" or patch.get("url") != PATCH_URL
            or patch.get("sha256") != PATCH_SHA256
            or set(patch) - {"file", "url", "sha256", "upstream_commit", "derivation"}
            or patch.get("upstream_commit", PATCH_COMMIT) != PATCH_COMMIT
            or patch.get("derivation", "Runtime-only diff; paths relative to gst-plugins-bad source root") !=
            "Runtime-only diff; paths relative to gst-plugins-bad source root"):
        raise ValueError("GstPlay patch differs from its upstream backport pin")
    if recipe.get("project_file") != RECIPE_PROJECT_FILE or set(recipe) != {"project_file", "sha256"}:
        raise ValueError("unexpected GstPlay recipe")
    if "cache_key" in data and (not isinstance(data["cache_key"], str) or
                                not re.fullmatch(r"[0-9a-f]{64}", data["cache_key"])):
        raise ValueError("invalid GstPlay cache key")
    checked(project_root, recipe, "project_file")
    if sha256(relative_file(project_root, PATCH_PROJECT_FILE)) != PATCH_SHA256:
        raise ValueError("project GstPlay patch differs from the receipt")
    archive = checked(path.parent, source)
    checked(path.parent, patch)
    outputs, licences = data.get("outputs", []), data.get("licence_files", [])
    if (not isinstance(outputs, list) or len(outputs) != 1 or not isinstance(outputs[0], dict)
            or set(outputs[0]) != {"file", "sha256"}):
        raise ValueError("unexpected GstPlay output records")
    if not re.fullmatch(
            r"bin/libgstplay-1\.0-0\.dll|lib/libgstplay-1\.0\.so\.0(?:\.\d+\.\d+)?", outputs[0].get("file", "")):
        raise ValueError("unexpected GstPlay output")
    checked(path.parent if output_root is None else output_root, outputs[0])
    if (not isinstance(licences, list) or len(licences) != 1 or not isinstance(licences[0], dict)
            or set(licences[0]) != {"file", "sha256"} or licences[0].get("file") != "COPYING"):
        raise ValueError("GstPlay needs its upstream COPYING notice")
    notice = checked(path.parent, licences[0])
    with tarfile.open(archive, "r|*") as source_tar:
        matches = 0
        for member in source_tar:
            if member.name == f"gst-plugins-bad-{version}/COPYING":
                if not member.isfile():
                    raise ValueError("GstPlay COPYING is not a regular source member")
                stream = source_tar.extractfile(member)
                with stream:
                    if hashlib.file_digest(stream, "sha256").hexdigest() != sha256(notice):
                        raise ValueError("GstPlay notice differs from the upstream source")
                matches += 1
        if matches != 1:
            raise ValueError("GstPlay source needs exactly one COPYING notice")
    return data


def read_receipt(path, *, output_root=None, project_root=ROOT):
    """Read checked public metadata without leaking local paths on missing inputs."""
    try:
        return _read_receipt(path, output_root=output_root, project_root=project_root)
    except (OSError, tarfile.TarError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid or missing GstPlay receipt material: {type(error).__name__}") from None
