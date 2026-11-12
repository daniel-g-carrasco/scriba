# Licensing and distribution

Scriba is copyright 2026 Daniel Grasso and is licensed under GPL-3.0-or-later.
The complete terms are in [LICENSE](../LICENSE); the grant and warranty notice
are in [COPYRIGHT](../COPYRIGHT). Earlier revisions published with an MIT notice
remain available under that original grant. Third-party copyrights and licences
are retained; the change does not relicense other people's components.

The Windows package contains GPL-covered libraries as well as LGPL and
permissively licensed components. The original source notices of JBIG-KIT 2.1,
LZO 2.10 and x264 allow GPL version 2 or later, even where package metadata is
less specific. GPLv3 is compatible with the Apache 2.0 components examined;
see [Apache's explanation](https://apache.org/licenses/GPL-compatibility.html).
GCC's runtime exception and PyInstaller's bootloader exception are retained.

The package's inventory records the actual installed package versions and
origins. A repository link or a licence label alone is not a corresponding
source distribution: the sources must match the shipped binaries, including
patches and the build and installation instructions. The source collection
process checks signed MSYS2 archives and matches the recipe hash against the
binary package's build information. It never executes downloaded recipes.

A public binary release is not ready until its corresponding source material,
notices and manifests have been checked and made available alongside it. The
release jobs collect and verify that material before uploading artifacts. The
release remains a draft until both platforms' binaries and source archives
have their signatures. Development artifacts are not a completed release.

Windows source collection preserves the exact package recipes, signed binary
and source provenance, upstream sources and the minimal FFmpeg build inputs.
Its manifest identifies any inputs requiring manual review; acquisition alone
does not replace that review.

FFmpeg remains the application's separately built LGPL configuration. Speech
models are downloaded directly from their upstream locations and are not
included in the application packages; their own terms are shown in the app.
The bundled public-domain speech sample retains its source and credit file.

For Linux, the Flatpak manifest pins the application components and their
checksums. GNOME Platform is a separate runtime obtained from Flathub; the app
package does not contain a copy of that runtime.
`tools/collect_linux_sources.py` checks the catalogued sources, wheel inputs,
vendored runtime files and application sources against the current build. Its
manifest records the bundle's SHA-256 and the public application commit. The
Flatpak repository imports that same signed bundle only after checking its
source archive and this association. The ONNX Runtime dependency inventory is
derived from upstream build recipes, not a producer-signed SBOM; the catalogue
retains that limitation and its provenance.

Corresponding-source archives are separate release downloads. They do not
increase the application installer or trigger an automatic source download.
