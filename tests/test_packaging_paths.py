import unittest
from pathlib import Path
from unittest import mock

from src.app import paths


class PackagedPathTests(unittest.TestCase):
    def test_program_dir_uses_executable_parent_when_frozen(self):
        with mock.patch.object(paths.sys, "frozen", True, create=True), \
             mock.patch.object(paths.sys, "executable", "/tmp/portable/Universal-Video-Downloader.exe"):
            self.assertEqual(paths.get_program_dir(), Path("/tmp/portable").resolve())

    def test_runtime_bin_dir_uses_meipass_when_frozen(self):
        with mock.patch.object(paths.sys, "frozen", True, create=True), \
             mock.patch.object(paths.sys, "_MEIPASS", "/tmp/_MEI123", create=True):
            self.assertEqual(paths.get_runtime_bin_dir(), Path("/tmp/_MEI123").resolve())


if __name__ == "__main__":
    unittest.main()
