"""Linux builds include their prerequisites; pull requests cannot publish."""

import re
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
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
        self.assertIn("needs: [test, scope]", found["flatpak"])
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


class PackagingOptimisationTest(unittest.TestCase):
    def test_only_packaging_is_conditional_and_git_history_is_available(self):
        linux_text = (WORKFLOWS / "build.yml").read_text(encoding="utf-8")
        windows_text = (WORKFLOWS / "windows.yml").read_text(encoding="utf-8")
        linux, windows = jobs(linux_text), jobs(windows_text)
        self.assertNotIn("paths-ignore", linux_text + windows_text)
        self.assertNotRegex(linux["test"], r"(?m)^    if:")
        self.assertNotRegex(windows["probe"], r"(?m)^    if:")
        self.assertIn("needs.scope.outputs.package != 'false'", linux["flatpak"])
        self.assertIn("needs.probe.outputs.package != 'false'", windows["package"])
        for scope in (linux["scope"], windows["probe"]):
            self.assertIn("fetch-depth: 0", scope)
            self.assertIn("tools/ci_build.py scope", scope)
            self.assertIn("outputs:", scope)
        self.assertIn("unittest discover -s tests", windows["probe"])
        for name in ("probe", "package"):
            self.assertRegex(windows[name], r"(?m)^            git$")

    def test_flatpak_state_cache_includes_ccache_and_exact_runtime_identity(self):
        flatpak = jobs((WORKFLOWS / "build.yml").read_text(encoding="utf-8"))["flatpak"]
        self.assertIn("path: .flatpak-builder", flatpak)
        self.assertIn("tools/ci_build.py flatpak-key", flatpak)
        self.assertIn("flatpak-builder --show-manifest", flatpak)
        self.assertIn("--show-commit org.gnome.Sdk//50", flatpak)
        self.assertIn("--show-commit org.gnome.Platform//50", flatpak)
        self.assertIn("--arch=x86_64", flatpak)
        self.assertIn("--ccache --rebuild-on-sdk-change", flatpak)
        self.assertNotIn("--ccache=false", flatpak)
        self.assertNotIn("--disable-cache", flatpak)
        self.assertNotIn("restore-keys:", flatpak)


class FlatpakSourceReliabilityTest(unittest.TestCase):
    def package(self):
        return jobs((WORKFLOWS / "build.yml").read_text(encoding="utf-8"))["flatpak"]

    def test_all_source_git_commands_trust_only_the_expected_checkout(self):
        package = self.package()
        collector = package.split("- name: Collect the corresponding sources", 1)[1]
        commands = re.findall(r"(?:^\s+|\$\()(git [^\n)]*)", collector, re.M)
        self.assertEqual(len(commands), 3)
        for command in commands:
            self.assertIn('-c safe.directory="$GITHUB_WORKSPACE"', command)
        self.assertNotIn("--global", collector)
        self.assertNotIn("safe.directory=*", collector)

    @unittest.skipIf(os.name == "nt", "the container ownership failure is on Linux")
    def test_the_archive_command_works_without_global_trust_for_a_different_owner(self):
        git, bash = shutil.which("git"), shutil.which("bash")
        if not git or not bash:
            self.skipTest("Git and bash are needed for the container ownership regression")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run([git, "init", "-q", str(root)], check=True, capture_output=True)
            (root / "README.md").write_text("public fixture\n", encoding="utf-8")
            subprocess.run([git, "-C", str(root), "add", "README.md"], check=True, capture_output=True)
            subprocess.run([git, "-C", str(root), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                            "-c", "commit.gpgsign=false", "-c", "core.hooksPath=" + str(root / "no-hooks"),
                            "commit", "-qm", "Public fixture"], check=True, capture_output=True)
            config = root / "empty-global-config"
            config.write_text("")
            environment = dict(os.environ, GITHUB_WORKSPACE=str(root), RUNNER_TEMP=str(root),
                               GIT_TEST_ASSUME_DIFFERENT_OWNER="1", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=str(config))
            environment.pop("GIT_CONFIG_COUNT", None)
            blocked = subprocess.run([git, "-C", str(root), "rev-parse", "HEAD"], env=environment,
                                     capture_output=True, text=True, timeout=10)
            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn("dubious ownership", blocked.stderr)
            collector = self.package().split("- name: Collect the corresponding sources", 1)[1]
            archive = next(line.strip() for line in collector.splitlines() if line.strip().startswith("git "))
            completed = subprocess.run([bash, "-c", archive], cwd=root, env=environment,
                                       capture_output=True, text=True, timeout=10)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue((root / "app-source.tar").is_file())

    def test_builder_cache_is_saved_after_validation_before_source_collection(self):
        package = self.package()
        pin = "55cc8345863c7cc4c66a329aec7e433d2d1c52a9"
        restore = package.index("uses: actions/cache/restore@" + pin)
        build = package.index("- name: Build\n")
        validated = package.index("- name: Check the package with Platform")
        save = package.index("uses: actions/cache/save@" + pin)
        collector = package.index("- name: Collect the corresponding sources")
        self.assertLess(restore, build)
        self.assertLess(build, validated)
        self.assertLess(validated, save)
        self.assertLess(save, collector)
        self.assertIn("id: flatpak-state", package[restore:build])
        after_validation = package[validated:collector]
        self.assertIn("if: steps.flatpak-state.outputs.cache-hit != 'true'", after_validation)
        self.assertIn("path: .flatpak-builder", after_validation)
        self.assertIn("key: ${{ steps.flatpak-cache.outputs.key }}", after_validation)
        self.assertIn("uses: actions/cache@" + pin, after_validation)
        self.assertIn("path: ${{ runner.temp }}/linux-source-cache", after_validation)
