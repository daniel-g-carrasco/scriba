"""Packaging can skip safe documentation; cache keys track build dependencies."""

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import _setup

SPEC = importlib.util.spec_from_file_location("ci_build", _setup.ROOT / "tools" / "ci_build.py")
ci = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ci
SPEC.loader.exec_module(ci)


class ScopeTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self._git = ci.git_binary()
        self.git("init", "-q")
        self.git("config", "user.name", "Daniel Grasso")
        self.git("config", "user.email", "code@danielgrasso.com")
        self.git("config", "core.autocrlf", "false")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "core.hooksPath", str(self.root / "no-hooks"))
        self.git("config", "core.fsmonitor", "false")
        self.commit("src/example.py", "original")
        self.base = self.git("rev-parse", "HEAD").strip()

    def git(self, *args):
        return subprocess.run([self._git, "-C", str(self.root), *args], check=True, capture_output=True,
                              text=True, timeout=10).stdout

    def commit(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        self.git("add", "--", relative)
        self.git("commit", "-qm", "Update fixture")
        return self.git("rev-parse", "HEAD").strip()

    def decision(self, head, event_name="push", ref="refs/heads/main", event=None):
        return ci.packaging_needed(event_name, ref, ({"before": self.base} if event is None else event), head, self.root)[0]

    def test_a_multi_commit_documentation_push_skips_packaging(self):
        self.commit("README.md", "read me")
        head = self.commit("docs/how-to.md", "instructions")
        self.assertFalse(self.decision(head))

    def test_code_licensing_metadata_and_workflows_always_package(self):
        for path in ("docs/licensing.md", "docs/LICENSING.md", "LICENSE", "COPYRIGHT", "LICENSES/COPYING",
                     "build-aux/flatpak/manifest.yaml", "data/app.metainfo.xml", ".github/workflows/build.yml",
                     "src/main.py", "tools/check.py", "site/index.html"):
            with self.subTest(path=path):
                self.assertFalse(ci.docs_only([path]))
        self.assertTrue(ci.docs_only(["README.md", "CONTRIBUTING.md", "docs/nested/example.md"]))

    def test_tag_and_manual_runs_always_package_even_if_the_event_file_is_absent(self):
        for event, ref in (("push", "refs/tags/v1.0.0"), ("workflow_dispatch", "refs/heads/main")):
            self.assertTrue(self.decision(self.base, event, ref, {}))

    def test_first_push_missing_commit_bad_sha_and_empty_diff_fail_closed(self):
        for before in ("0" * 40, "f" * 40, "--bad-option", self.base):
            with self.subTest(before=before):
                self.assertTrue(self.decision(self.base, event={"before": before}))
        self.assertTrue(self.decision(self.base, "unknown-event"))

    def test_a_pull_request_compares_the_checked_out_merge_with_the_event_base(self):
        head = self.commit("docs/help.md", "help")
        event = {"pull_request": {"base": {"sha": self.base}}}
        self.assertFalse(self.decision(head, "pull_request", "refs/pull/1/merge", event))
        self.assertTrue(self.decision(head, "pull_request", "refs/pull/1/merge", {"pull_request": {}}))

    def test_renaming_code_into_docs_does_not_hide_the_deleted_source(self):
        (self.root / "docs").mkdir()
        self.git("mv", "src/example.py", "docs/example.md")
        self.git("commit", "-qm", "Move fixture")
        self.assertTrue(self.decision(self.git("rev-parse", "HEAD").strip()))

    def test_unusual_names_are_data_and_cannot_inject_commands_or_outputs(self):
        self.assertFalse(ci.docs_only(["../docs/guide.md"]))
        self.assertFalse(ci.docs_only(["docs\\guide.md"]))
        self.assertFalse(ci.docs_only(["/docs/guide.md"]))
        self.assertTrue(ci.docs_only(["docs/notes\nwith a newline.md"]))
        self.assertTrue(self.decision("$(touch marker)"))
        self.assertFalse((self.root / "marker").exists())

    def test_a_different_checkout_or_modified_tracked_file_never_skips_packaging(self):
        docs = self.commit("docs/help.md", "help")
        self.commit("src/example.py", "new code")
        self.assertTrue(self.decision(docs))
        head = self.git("rev-parse", "HEAD").strip()
        self.base = head
        docs = self.commit("docs/help.md", "more help")
        (self.root / "src/example.py").write_text("uncommitted code")
        self.assertTrue(self.decision(docs))

    def test_git_failure_never_produces_a_skip(self):
        with mock.patch.object(ci.subprocess, "run", side_effect=OSError("git unavailable")):
            self.assertTrue(self.decision(self.base))

    def test_cli_writes_constant_github_outputs_and_defaults_to_packaging_on_bad_event_data(self):
        head = self.commit("docs/help.md", "help")
        event, output = self.root / "event.json", self.root / "github-output"
        args = ["scope", "--root", str(self.root), "--event-name", "push", "--ref", "refs/heads/main",
                "--head", head, "--event-path", str(event), "--output", str(output)]
        event.write_text("{broken")
        self.assertEqual(ci.main(args), 0)
        event.write_text(json.dumps({"before": self.base}))
        self.assertEqual(ci.main(args), 0)
        self.assertEqual(output.read_text().splitlines(), ["package=true", "package=false"])


class CacheKeyTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.manifest = {"app-id": "org.example.Scriba", "runtime": "org.gnome.Platform", "runtime-version": "50",
                         "sdk": "org.gnome.Sdk", "modules": [
                             {"name": "dependency", "sources": [{"type": "archive", "url": "https://example.invalid/source.tar.xz",
                                                                   "sha256": "a" * 64}]},
                             {"name": "scriba", "sources": [{"type": "dir", "path": "../.."}]}]}

    def key(self, **overrides):
        args = {"sdk_commit": "1" * 64, "runtime_commit": "2" * 64, "arch": "x86_64",
                "builder_version": "flatpak-builder 1.4.12"}
        args.update(overrides)
        return ci.flatpak_key(self.manifest, self.root, **args)

    def test_app_source_and_commands_do_not_invalidate_dependency_cache(self):
        before = self.key()
        self.manifest["modules"][-1]["sources"][0]["path"] = "another checkout"
        self.manifest["modules"][-1]["build-commands"] = ["copy different app sources"]
        (self.root / "application.py").write_text("new application code")
        self.assertEqual(self.key(), before)

    def test_runtime_sdk_arch_builder_flags_and_dependency_versions_change_the_key(self):
        before = self.key()
        for changed in ({"sdk_commit": "3" * 64}, {"runtime_commit": "4" * 64},
                        {"arch": "aarch64"}, {"builder_version": "flatpak-builder 1.4.13"}):
            with self.subTest(changed=changed):
                self.assertNotEqual(self.key(**changed), before)
        self.manifest["runtime-version"] = "51"
        self.assertNotEqual(self.key(), before)
        self.manifest["runtime-version"] = "50"
        self.manifest["build-options"] = {"cflags": "-O3"}
        self.assertNotEqual(self.key(), before)
        self.manifest.pop("build-options")
        self.manifest["modules"][0]["sources"][0]["sha256"] = "b" * 64
        self.assertNotEqual(self.key(), before)

    def test_local_dependency_patch_bytes_are_in_the_key(self):
        directory = self.root / "build-aux" / "flatpak"
        directory.mkdir(parents=True)
        patch = directory / "fix.patch"
        patch.write_text("old patch")
        self.manifest["modules"][0]["sources"].append({"type": "patch", "path": "fix.patch"})
        before = self.key(source_base=directory)
        patch.write_text("new patch, same filename")
        self.assertNotEqual(self.key(source_base=directory), before)

    def test_a_local_dependency_cannot_read_outside_the_checkout(self):
        self.manifest["modules"][0]["sources"].append({"type": "file", "path": "../secret"})
        with self.assertRaises(ValueError):
            self.key()

    def test_missing_app_module_and_unexpanded_manifests_cannot_reuse_cache(self):
        self.manifest["modules"] = ["included.json"]
        with self.assertRaises(ValueError):
            self.key()

    def test_invalid_runtime_commits_cannot_reuse_cache(self):
        with self.assertRaises(ValueError):
            self.key(sdk_commit="")
        with self.assertRaises(ValueError):
            self.key(arch="x86_64\nkey=wrong")
