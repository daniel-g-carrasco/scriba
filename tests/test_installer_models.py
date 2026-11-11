"""Static installer confinement: user models are outside every write/delete target.

The installer is never executed. The operative sections are allowlisted,
including Pascal file operations, so a new target requires review here.
"""

import re
import unittest
from pathlib import Path

import _setup

from scriba import EXE_NAME, SLUG
from scriba.app import models


class InstallerModelsTest(unittest.TestCase):
    def test_setup_only_replaces_its_own_files_and_never_targets_the_users_models(self):
        text = (_setup.ROOT / "build-aux" / "windows" / f"{SLUG}.iss").read_text(encoding="utf-8")
        sections, current = {}, ""
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith((";", "#")):
                continue
            if line.startswith("["):
                current = line.lower()
                sections[current] = []
            elif current:
                sections[current].append(line)
        self.assertEqual(sections["[installdelete]"], ['Type: filesandordirs; Name: "{app}\\_internal"'])
        self.assertEqual(len(sections["[files]"]), 1)
        self.assertIn('DestDir: "{app}";', sections["[files]"][0])
        self.assertEqual({re.search(r'Filename: "([^"]+)"', row).group(1) for row in sections["[run]"]},
                         {f"{{app}}\\{EXE_NAME}.exe"})
        self.assertNotIn("[uninstalldelete]", sections)
        self.assertNotIn("[dirs]", sections)
        code = "\n".join(sections["[code]"])
        self.assertEqual(re.findall(r"\b(DeleteFile|DelTree|FileCopy|RenameFile|ForceDirectories|Exec|ShellExec)\s*\(", code,
                                    re.IGNORECASE), [])
        given = models.folder({"SCRIBA_MODELS_DIR": "C:/User Models"})
        self.assertEqual(str(given).replace("\\", "/"), "C:/User Models")
        default = models.folder({"APPDATA": "C:/Profile/Roaming", "LOCALAPPDATA": "C:/Profile/Local", "HOME": "C:/Profile"})
        expected = Path(f"C:/Profile/Local/{EXE_NAME}/cache/models") if _setup.WINDOWS else \
            Path(f"C:/Profile/.cache/{SLUG}/models")
        self.assertEqual(default, expected)
        self.assertNotIn("_internal", str(default))
