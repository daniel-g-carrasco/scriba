"""The installer checks identity and release before touching installed files."""

import re
import unittest

import _setup


SCRIPT = _setup.ROOT / 'build-aux/windows/scriba.iss'


def routine(source, name):
    found = re.search(r'(?mi)^(?:function|procedure) ' + name + r'\b.*?(?=^(?:function|procedure) |\Z)', source, re.S)
    if not found:
        raise AssertionError('missing installer routine: ' + name)
    return found.group()


class PreflightSafetyTest(unittest.TestCase):
    def setUp(self):
        self.script = SCRIPT.read_text(encoding='utf-8')

    def test_exclusive_existing_file_check_requires_read_and_write_and_closes_its_handle(self):
        guard = routine(self.script, 'CanReplaceInstalledFile')
        self.assertIn('GENERIC_READ_WRITE', guard)
        self.assertIn('GENERIC_READ_WRITE = $C0000000', self.script)
        self.assertIn('OPEN_EXISTING = 3', self.script)
        self.assertRegex(guard, r'CreateFileW\(Path, GENERIC_READ_WRITE, 0, 0, OPEN_EXISTING, 0, 0\)')
        self.assertIn('DLLGetLastError', guard)
        self.assertIn('ERROR_FILE_NOT_FOUND', guard)
        self.assertIn('ERROR_PATH_NOT_FOUND', guard)
        self.assertIn('CloseNativeHandle', guard)
        self.assertNotIn('WriteFile', guard)
        self.assertNotIn('CREATE_ALWAYS', self.script)

    def test_only_an_installed_executable_can_own_the_update_window(self):
        identity = routine(self.script, 'InstalledWindowProcess')
        self.assertIn('GetWindowThreadProcessId', identity)
        self.assertIn('QueryFullProcessImageNameW', identity)
        self.assertIn("ExpandConstant('{app}\\Scriba.exe')", identity)
        self.assertIn('CompareText', identity)
        self.assertIn('CloseNativeHandle', identity)
        self.assertIn('SYNCHRONIZE_ACCESS', identity)

    def test_waiting_is_bounded_and_checks_the_process_and_both_installed_executables(self):
        wait = routine(self.script, 'WaitForInstalledFiles')
        self.assertIn('GetTickCount64', wait)
        self.assertIn('PREFLIGHT_TIMEOUT_MS = 30000', self.script)
        self.assertIn('WaitForSingleObject', wait)
        self.assertIn("ExpandConstant('{app}\\Scriba.exe')", wait)
        self.assertIn("ExpandConstant('{app}\\_internal\\gdbus.exe')", wait)
        self.assertNotIn('TerminateProcess', self.script)
        self.assertNotIn('taskkill', self.script.lower())
        self.assertIn('CloseApplications=yes', self.script)

    def test_restart_intent_survives_repeated_preflight_and_old_detection_is_removed(self):
        preflight = routine(self.script, 'PrepareToInstall')
        self.assertIn('WasRunning := True', preflight)
        self.assertNotIn('WasRunning := False', preflight)
        self.assertNotIn("CheckForMutexes('')", self.script)
        self.assertIn('WaitForInstalledFiles', preflight)
        self.assertIn("CustomMessage('UpdateFilesBusy')", preflight)

    def test_only_an_accepted_idle_protocol_can_request_shutdown(self):
        request = routine(self.script, 'RequestIdleUpdate')
        self.assertIn('io.github.daniel_g_carrasco.scriba.prepare-update.v1', request)
        self.assertIn('SendMessageTimeoutW', request)
        self.assertIn('2000, Reply', request)
        self.assertIn('UPDATE_ACCEPTED = 1', self.script)
        self.assertIn('UPDATE_BUSY = 2', self.script)
        preflight = routine(self.script, 'PrepareToInstall')
        self.assertLess(preflight.index('InstalledWindowProcess'), preflight.index('RequestIdleUpdate'))
        accepted = preflight.split('if Reply = UPDATE_ACCEPTED then begin', 1)[1].split('end;', 1)[0]
        self.assertIn('Break;', accepted)
        self.assertIn('if Reply <> UPDATE_BUSY', preflight)
        self.assertIn("CustomMessage('UpdateCloseManually')", preflight)
        self.assertIn("CustomMessage('UpdateWorkBusy')", preflight)
        self.assertNotIn('PostMessage', self.script)
        self.assertNotIn('WM_ENDSESSION', self.script)
        self.assertNotIn('gdbus call', self.script)

    def test_preflight_cannot_delete_or_replace_files_and_errors_are_localised(self):
        preflight = routine(self.script, 'PrepareToInstall')
        for mutation in ('DeleteFile(', 'DelTree(', 'SaveStringToFile(', 'RegWrite', 'Exec('):
            self.assertNotIn(mutation, preflight)
        for message in ('UpdateCloseManually', 'UpdateWorkBusy', 'UpdateFilesBusy'):
            self.assertIn('italian.' + message + '=', self.script)
            self.assertIn('english.' + message + '=', self.script)
        self.assertIn('Check: AppWasRunning', self.script)
