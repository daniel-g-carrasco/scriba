# Code signing policy

## Signature of the releases (minisign)

The Windows installer, portable ZIP, Flatpak bundle and corresponding-source
archives carry [minisign](https://jedisct1.github.io/minisign/) signatures. The workflow
expects the `MINISIGN_SECRET_KEY` repository secret, checks each signature
with the public key and uploads `<file>.minisig` beside the file; its trusted
comment names the file. The Windows updater already checks this signature
before running an update. Pull requests do not run the signing job, and
Linux/Flatpak publication jobs require a version tag and their signing keys;
a main push or pull request cannot publish.

Public key (key id `CEFFF9269F08F845`):

    RWRF+AifJvn/zhz0o7MBObIYHgw9+sWoc4GaBK+qYnJY0AqeJm540y0a

To check a download by hand, with the `.minisig` of the release in the same
folder:

    minisign -Vm scriba-setup-vX.Y.Z-windows-x64.exe -P RWRF+AifJvn/zhz0o7MBObIYHgw9+sWoc4GaBK+qYnJY0AqeJm540y0a

This is not an Authenticode signature: Windows does not know it, and
SmartScreen still warns when the installer is started from a download.

## Team roles

- Committers and reviewers: [Daniel Grasso](https://github.com/daniel-g-carrasco)
- Approvers: [Daniel Grasso](https://github.com/daniel-g-carrasco)

Releases require maintainer approval and a version tag. Published packages
and signatures are available on [GitHub Releases](https://github.com/daniel-g-carrasco/scriba/releases).

## Privacy policy

Recordings and transcripts are processed locally and are not uploaded.
Model downloads contact the model authors' hosts. The optional Windows
update check asks GitHub Releases (api.github.com) whether a newer version
exists, without sending recordings or transcript text. It can be turned off
in Preferences ("Check for updates at start-up"). Opening a source or release
link uses the user's browser.
