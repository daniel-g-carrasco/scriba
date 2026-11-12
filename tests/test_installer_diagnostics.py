"""Installer failures retain complete logs without changing the update scenario."""

import re
import unittest

import _setup


def step(name):
    text = (_setup.ROOT / '.github/workflows/windows.yml').read_text(encoding='utf-8')
    found = re.search(r'^      - name: ' + re.escape(name) + r'\n(.*?)(?=^      - |^  release:|\Z)', text, re.M | re.S)
    if found is None:
        raise AssertionError('missing workflow step: ' + name)
    return found.group(1)


UPDATE = 'Update over a running app (Setup closes it, starts it again)'


class InstallerDiagnosticsTest(unittest.TestCase):
    def test_both_setup_logs_are_printed_in_finally_without_filtering(self):
        body = step(UPDATE)
        self.assertIn('/LOGCLOSEAPPLICATIONS', body)
        self.assertIn('$initialLog', body)
        self.assertIn('$updateLog', body)
        self.assertIn('finally {', body)
        final = body.rsplit('finally {', 1)[1]
        self.assertIn('@($initialLog, $updateLog)', final)
        self.assertIn('Get-Content -LiteralPath $log', final)
        self.assertNotIn('Select-String', body)
        self.assertIn('$held.Dispose()', final)

    def test_process_diagnostics_include_identity_and_paths_without_commands_or_environment(self):
        body = step(UPDATE)
        self.assertIn('Get-CimInstance Win32_Process', body)
        self.assertIn('Name, ProcessId, ParentProcessId, ExecutablePath', body)
        self.assertIn("Save-Processes 'before-install'", body)
        self.assertIn("Save-Processes 'before-update'", body)
        self.assertIn("Save-Processes 'after-update'", body)
        self.assertIn("Save-Processes 'finally'", body)
        self.assertNotIn('CommandLine', body)
        self.assertNotIn('Get-ChildItem Env:', body)

    def test_diagnostic_artifacts_survive_failures_and_are_separate_from_binaries(self):
        artifact = step('Preserve installer diagnostics')
        self.assertIn('if: always()', artifact)
        self.assertIn('actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9', artifact)
        self.assertIn('name: windows-installer-diagnostics', artifact)
        self.assertIn('path: installer-diagnostics/', artifact)
        self.assertNotIn('dist/', artifact)
        text = (_setup.ROOT / '.github/workflows/windows.yml').read_text(encoding='utf-8')
        binary = text.split('name: scriba-windows\n', 1)[1].split('\n  release:', 1)[0]
        self.assertNotIn('always()', binary)

    def test_existing_waits_and_shutdown_behaviour_are_preserved(self):
        body = step(UPDATE)
        self.assertEqual(body.count('Start-Sleep 15'), 2)
        self.assertIn('WaitForExit(300000)', body)
        self.assertIn("'/VERYSILENT', '/CURRENTUSER', '/SUPPRESSMSGBOXES', '/NORESTART'", body)
        self.assertNotIn('/FORCECLOSEAPPLICATIONS', body)
        self.assertNotIn('-Force', body)
        self.assertEqual(body.count('Get-Process Scriba | Stop-Process'), 1)
