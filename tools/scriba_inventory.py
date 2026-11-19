"""Adapt Scriba's verified Windows build inputs to the model's bundle inventory."""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath

try:
    import package_inventory as common
except ModuleNotFoundError:
    from tools import package_inventory as common

LICENSES = "_internal/licenses/"
PACKAGES = LICENSES + "PACKAGES.json"

COMMUNITY = {
    "onnxruntime.dll": ("community-onnxruntime", "runtime", ("onnxruntime-MIT.txt", "onnxruntime-ThirdPartyNotices.txt")),
    "onnxruntime_providers_shared.dll": ("community-onnxruntime", "runtime",
                                       ("onnxruntime-MIT.txt", "onnxruntime-ThirdPartyNotices.txt")),
    "dxcompiler.dll": ("community-webgpu", "plugin", ("webgpu-MIT.txt", "webgpu-ThirdPartyNotices.txt")),
    "onnxruntime_providers_webgpu.dll": ("community-webgpu", "plugin", ("webgpu-MIT.txt", "webgpu-ThirdPartyNotices.txt")),
    "vulkan-1.dll": ("vulkan-loader", "loader", ("Vulkan-Loader-LICENSE.txt", "VulkanRT-License.txt")),
}


def seal(bundle: Path, revision: str) -> dict:
    """Keep the input provenance and seal every byte of the final payload."""
    previous = json.loads((bundle / PACKAGES).read_bytes())
    payload = common.payload(bundle)
    components = {}
    notices = {}
    files = {}
    issues = []

    def register(kind, name, version, notice_paths=(), **metadata):
        key = f"{kind}:{name}"
        if key in components:
            return key
        available = []
        for path in notice_paths:
            if path in payload:
                available.append(path)
                notices[path] = payload[path]
            else:
                issues.append({"target": key, "reason": f"missing notice: {path}"})
        if kind != "generated" and not available:
            issues.append({"target": key, "reason": "no original licence text"})
        components[key] = dict(kind=kind, name=name, version=version, license_files=available, **metadata)
        return key

    def add(target, component, source, checksum):
        if not target.startswith("module:") and payload.get(target) != checksum:
            raise common.InventoryError(f"bundle changed after provenance was recorded: {target}")
        files[target] = dict(target=target, component=component, source=source, sha256=checksum)

    for record in previous["packages"]:
        name = record["package"]
        short = name.removeprefix("mingw-w64-ucrt-x86_64-")
        paths = [f"{LICENSES}{short}/{common.package_notice_path(member)}" for member in record["licence_files"]]
        paths += [LICENSES + p for p in record.get("upstream_licence_files", [])]
        key = register("msys2", name, record["version"], paths, base=record["base"], licenses=record["licences"])
        for field, prefix in (("origins", ""), ("module_origins", "module:")):
            for target, origin in record[field].items():
                add(prefix + target, key, origin["package_file"], origin["sha256"])

    project = register("project", "scriba", revision, [LICENSES + "Scriba-GPL-3.0-or-later.txt"])
    generated = register("generated", "build", revision)
    for record in previous["generated_files"]:
        add(record["file"], generated, record["file"], record["sha256"])
    for record in previous["custom_components"]:
        receipt = record["receipt"]
        paths = [LICENSES + "gstplay/" + item["file"] for item in receipt["licence_files"]]
        for target, output in zip(record["files"], receipt["outputs"], strict=True):
            key = register("custom", "gstplay", receipt["version"], paths)
            add(target, key, output["file"], output["sha256"])

    ffmpeg = (bundle / (LICENSES + "ffmpeg/BUILD.txt")).is_file()
    community_path = "_internal/community-runtime/community-runtime-windows.json"
    community = json.loads((bundle / community_path).read_bytes())
    for target, checksum in payload.items():
        if target in files or target in notices:
            continue
        path = PurePosixPath(target)
        if ffmpeg and target in ("_internal/ffmpeg.exe", "_internal/ffprobe.exe"):
            key = register("custom", "ffmpeg", "9.0.2", [LICENSES + "ffmpeg/COPYING.LGPLv2.1"])
        elif path.parent.as_posix() == "_internal/community-runtime" and path.name in COMMUNITY:
            name, receipt_key, texts = COMMUNITY[path.name]
            value = community[receipt_key]
            version = value["version"] if isinstance(value, dict) else value.split()[1]
            key = register("custom", name, version, [LICENSES + text for text in texts])
            if checksum != community["files"][path.name]["sha256"]:
                raise common.InventoryError(f"community runtime checksum differs: {target}")
        elif (target.startswith((LICENSES, "_internal/scriba/", "_internal/share/locale/it/LC_MESSAGES/scriba.mo",
                                  "_internal/share/icons/hicolor/")) or target == community_path):
            key = project
        elif target in ("Scriba.exe", "scriba-cli.exe", "_internal/base_library.zip"):
            key = generated
        else:
            key = None
            issues.append({"target": target, "reason": "no recorded origin"})
        add(target, key, target, checksum)

    manifest = dict(schema_version=1, platform="windows-x64", components=components,
                    files=sorted(files.values(), key=lambda item: item["target"]),
                    notices=notices, issues=issues, payload=payload)
    path = bundle / common.MANIFEST_PATH
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    common.verify_bundle(bundle, manifest)
    return manifest
