"""Bind Scriba's Windows source collection to the sealed build and both downloads."""

import argparse
import json
import re
from pathlib import Path

from package_inventory import MANIFEST_PATH, sha256, verify_bundle, verify_portable
from scriba_inventory import PACKAGES


def pair(bundle, portable, installer, sources, commit):
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit):
        raise ValueError("supply the exact build commit")
    inventory = (bundle / MANIFEST_PATH).read_bytes()
    data = json.loads(inventory)
    verify_bundle(bundle, data, strict=True)
    verify_portable(portable, inventory)
    compiled_from = data["components"]["project:scriba"]["version"]
    if compiled_from != commit:
        raise ValueError("bundle inventory belongs to a different commit")
    source_manifest = sources / "manifest.json"
    collected = json.loads(source_manifest.read_bytes())
    if (not collected["collection_complete"] or collected["errors"]
            or collected["inventory_sha256"] != sha256(bundle / PACKAGES)):
        raise ValueError("source collection does not cover the verified Windows build")
    return {
        "schema_version": 1, "app_commit": commit,
        "bundle_inventory_sha256": sha256(bundle / MANIFEST_PATH),
        "source_manifest_sha256": sha256(source_manifest),
        "downloads": {path.name: sha256(path) for path in (portable, installer)},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("bundle", "portable", "installer", "sources"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--commit", required=True)
    args = parser.parse_args()
    try:
        data = pair(args.bundle, args.portable, args.installer, args.sources, args.commit)
        with (args.sources / "WINDOWS-PAIR.json").open("x", encoding="utf-8", newline="\n") as output:
            json.dump(data, output, indent=2)
            output.write("\n")
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f"Windows source pairing failed: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
