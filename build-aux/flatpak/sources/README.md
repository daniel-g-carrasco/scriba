# Corresponding sources for the Linux package

`linux-corresponding-sources.json` pins 49 source artefacts: native dependency
sources and recipes, seven Python source distributions, whisper.cpp and
SPIRV-Headers. Each URL has an exact byte count and SHA-256; where the upstream
recipe supplies SHA-512, the collector checks that too. Recipe files fetched
from the GitHub blob API retain their required public `Accept` header.

The catalogue preserves the distinction between measured binary associations
and dependency membership inferred from upstream recipes. It is an unsigned,
recipe-derived inventory, not a vendor-signed SBOM or an attestation that the
binaries can be reproduced byte for byte. The seven wheel-vendored GCC runtime
files have exact binary/source mappings. ONNX Runtime retains its embedded
source commit and the resolved port recipes. Declared licence values and audit
limits are retained without changing their meaning. Acquiring a pinned SRPM
checks its digest; it does not add an RPM signature verification claim.

`tools/collect_linux_sources.py` treats archives and recipes as data. It never
executes package recipes, extracts dependency archives or creates an archive of
the working checkout. It accepts only HTTPS and refuses changed cache bytes,
missing references, unsafe paths, partial transfers and mismatched build
inputs. Temporary transfer files are removed on failure or cancellation.

The Linux build workflow supplies the expanded Flatpak manifest, exact runtime
and SDK commits, the current build directory, downloaded wheel/ONNX inputs,
the bundle and a `git archive` produced from the public CI checkout. The
collector verifies these inputs before setting `publishable` in its output
`manifest.json`. It checks the supplied application source files against the
installed application and rejects an archive that omits installed Python
source files. The output includes all source artefacts, recipes, the public
application archive and their provenance report.

Use separate output and cache directories outside the checkout. An explicit
`--acquire-only` run can populate and inspect sources before packaging, but its
report remains non-publishable. No release job uses that option. A failed run
returns a nonzero exit status and cannot pass the source gate; choose a new
output revision after a report has been written.

After the output directory has been archived, the following command reads its
root manifest without extraction and checks that it belongs to the bundle and
public source commit being released:

```sh
python3 tools/collect_linux_sources.py \
  --verify-pair scriba-vVERSION-linux-x86_64-sources.tar.xz \
  --bundle scriba.flatpak --app-commit PUBLIC_COMMIT
```

This association check complements the release signature checks. The build
uploads an unsigned source archive on normal package builds and pull requests;
a version tag signs both the Flatpak and its source archive. The release stays
a draft until all ten binary, source and signature assets are present.
