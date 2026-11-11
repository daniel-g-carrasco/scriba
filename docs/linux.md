# Linux build and runtime checks

Scriba targets x86_64 with GNOME Platform 50 (Python 3.13). The manifest is
`build-aux/flatpak/io.github.daniel_g_carrasco.scriba.yaml`. Linux packages are
currently development builds; publication requires a separately authorised version tag; a main push produces
checks and artifacts without a release.

The package builds whisper.cpp 1.9.4 with Vulkan and dynamically selected CPU
variants. `GGML_NATIVE=OFF` prevents the build machine's instruction set becoming
a requirement. SPIRV-Headers is a build dependency because the SDK's Vulkan
compiler alone does not provide its CMake headers. The ONNX Runtime 1.29.0 C
library supplies API 29; Scriba does not need its Python extension. NumPy 2.5.4,
SciPy 1.18.1 and scikit-learn 1.9.1, with their four Python dependencies, are
installed from fixed CPython 3.13 wheels in `python-numerical.json`. Every source
and wheel has a SHA-256 hash; the build needs no network after fetching sources.
This manifest has x86_64 wheels and an x86_64 ONNX library: it does not claim
ARM support.

FFmpeg and GStreamer come from the Platform. The runtime check requires FFmpeg's
LGPL configuration and the recording plugins. Engine licences are installed in
`/app/share/licenses`; numerical wheels retain their licence files in the Python
distributions. The runtime distributes its own component licences. Speech models
are downloaded separately, checked against the catalogue hashes, and retained in
the user's cache. Updating the application does not replace that cache.

Install the runtime, SDK and builder for the current user, then build from the
repository root:

```sh
flatpak install --user flathub org.gnome.Platform//50 org.gnome.Sdk//50 org.flatpak.Builder
flatpak run org.flatpak.Builder --user --install --disable-rofiles-fuse \
  --force-clean --ccache=false --repo=repo build-dir \
  build-aux/flatpak/io.github.daniel_g_carrasco.scriba.yaml
flatpak run --command=python3 io.github.daniel_g_carrasco.scriba \
  /app/share/scriba/check_flatpak.py
flatpak run io.github.daniel_g_carrasco.scriba
flatpak build-bundle repo scriba.flatpak io.github.daniel_g_carrasco.scriba stable \
  --runtime-repo=https://dl.flathub.org/repo/flathub.flatpakrepo
```

Use a build directory under the home folder with `org.flatpak.Builder`; its
sandbox cannot access arbitrary host temporary directories. The installed-app
check above uses Platform rather than the SDK and checks numerical imports,
ONNX's C API, both whisper programs, FFmpeg's licence and recording plugins
without loading models, playing sound or opening recording devices.

The permissions have specific purposes:

- Wayland, fallback X11, IPC and DRI provide the window and Vulkan acceleration.
- The PulseAudio socket also works with PipeWire and exposes the microphone and
  output monitor sources. Device selection remains visible to the user.
- Network access downloads models. Speech and transcripts are processed locally.
- Writable `host` filesystem access supports new transcript revisions beside
  recordings on local or removable disks, and saving recordings in the selected
  folder. The file portal grants a selected file, not its parent and sibling
  outputs. Consequently this package can read and write ordinary user files
  without the portal; it is not limited to a selected recording.
- Read-only GTK configuration access uses the desktop's accent colours.

The source tests need PyGObject, GTK 4, libadwaita, GStreamer, FFmpeg, NumPy,
SciPy and scikit-learn. `test.yml` installs these explicitly and runs the full
suite on a display, plus a direct window module run that cannot silently skip
all GUI tests. `build.yml` builds a Flatpak artifact and repeats the dependency
check against Platform. Pull requests receive no publishing credentials.

The signed repository workflow and release job run only for `v*` tags, after
their checks. Creating and pushing a release tag is a separate action. The
repository needs the dedicated repository
key in `FLATPAK_GPG_PRIVATE_KEY`, its complete fingerprint in the repository
variable `FLATPAK_GPG_KEY_ID`, and GitHub Pages configured for `gh-pages`.
Release bundles also need `MINISIGN_SECRET_KEY`; no private key belongs in the
checkout. The existing complete-release check requires the Linux bundle,
Windows installer and portable archive, with all three signatures, before
making a draft release public.

## Repository signing key

The dedicated Scriba Flatpak repository signing key has fingerprint:

```text
2A17 1ED4 30D6 082E DED4 73B5 DFA3 8180 5867 7201
```

This identifies the key prepared for the first signed repository publication.
No public Flatpak repository has been released yet. The minisign key for release
files is separate; see [CODE_SIGNING.md](../CODE_SIGNING.md).
