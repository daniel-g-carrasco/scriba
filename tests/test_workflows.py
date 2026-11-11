"""Linux builds include their prerequisites; pull requests cannot publish."""

import re
import unittest

import _setup

WORKFLOWS = _setup.ROOT / ".github" / "workflows"


def section(text, heading):
    return re.search(rf"^{heading}:\n(.*?)(?=^[^ #\n][^\n]*:|\Z)", text, re.M | re.S).group(1)


def jobs(text):
    return dict(re.findall(r"^  ([\w-]+):\n(.*?)(?=^  [\w-]+:\n|\Z)", section(text, "jobs"), re.M | re.S))


class WorkflowGatesTest(unittest.TestCase):
    def test_linux_checks_and_builds_run_and_publication_requires_a_tag(self):
        text = (WORKFLOWS / "build.yml").read_text(encoding="utf-8")
        for trigger in ("push", "pull_request", "workflow_dispatch"):
            self.assertRegex(section(text, "on"), rf"(?m)^  {trigger}:")
        found = jobs(text)
        for name in ("test", "flatpak"):
            self.assertNotIn("${{ false }}", found[name])
            self.assertNotIn("secrets.", found[name])
            self.assertNotIn("contents: write", found[name])
        self.assertIn("needs: test", found["flatpak"])
        self.assertIn("/app/share/scriba/check_flatpak.py", found["flatpak"])
        self.assertIn("org.gnome.Platform//50", found["flatpak"])
        release_gate = re.search(r"^    if:\s*(.+)$", found["release"], re.M).group(1)
        self.assertIn("startsWith(github.ref, 'refs/tags/v')", release_gate)
        self.assertIn("github.event_name != 'pull_request'", release_gate)
        self.assertIn("needs: [test, flatpak]", found["release"])
        self.assertIn("minisign -V", found["release"])
        self.assertIn('tags: ["v*"]', section(text, "on"))
        remote = (WORKFLOWS / "flatpak-repo.yml").read_text(encoding="utf-8")
        remote_triggers = section(remote, "on")
        self.assertIn('tags: ["v*"]', remote_triggers)
        self.assertNotIn("branches:", remote_triggers)
        self.assertNotRegex(remote_triggers, r"(?m)^  (pull_request|schedule):")
        publish = jobs(remote)["publish"]
        self.assertIn("if: startsWith(github.ref, 'refs/tags/v')", publish)
        self.assertIn("needs: [test, build]", publish)
        self.assertIn("flatpak build-import-bundle", publish)
        self.assertIn("Good signature", publish)
        self.assertNotIn("flatpak-builder", publish)
        self.assertNotIn("secrets.", jobs(remote)["build"])
        self.assertNotIn("contents: write", jobs(remote)["build"])
        self.assertIn('--gpg-sign="$FLATPAK_GPG_KEY_ID"', publish)
        self.assertIn("FLATPAK_GPG_PRIVATE_KEY", remote)
        self.assertIn("FLATPAK_GPG_KEY_ID", remote)

    def test_linux_suite_has_numerical_audio_and_real_display_dependencies(self):
        text = (WORKFLOWS / "test.yml").read_text(encoding="utf-8")
        found = jobs(text)
        for name in ("core", "gtk"):
            self.assertNotIn("${{ false }}", found[name])
            for package in ("python3-numpy", "python3-scipy", "python3-sklearn", "ffmpeg"):
                self.assertIn(package, found[name])
        for package in ("gir1.2-gstreamer-1.0", "gstreamer1.0-pulseaudio",
                        "libgtk-4-media-gstreamer", "xvfb"):
            self.assertIn(package, found["gtk"])
        self.assertIn("unittest discover -s tests", found["gtk"])
        self.assertIn("unittest -v test_window", found["gtk"])
        self.assertIn("persist-credentials: false", text)

    def test_windows_checks_pull_requests_with_read_only_builds_and_no_release_job(self):
        text = (WORKFLOWS / "windows.yml").read_text(encoding="utf-8")
        triggers = section(text, "on")
        self.assertRegex(triggers, r"(?m)^  pull_request:")
        self.assertNotIn("pull_request_target:", triggers)
        self.assertRegex(section(text, "permissions"), r"(?m)^  contents: read$")
        found = jobs(text)
        for name in ("probe", "package"):
            self.assertNotIn("secrets.", found[name])
            self.assertNotIn("contents: write", found[name])
        gate = re.search(r"^    if:\s*(.+)$", found["release"], re.M).group(1)
        self.assertIn("github.event_name != 'pull_request'", gate)
        self.assertIn("startsWith(github.ref, 'refs/tags/v')", gate)
