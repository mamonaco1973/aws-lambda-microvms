"""Local guard tests. No AWS calls and no privileged mounts.

Run: python3 -m unittest discover -s tests -v
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which('bash') or (r'C:\Program Files\Git\bin\bash.exe' if os.name == 'nt' else None)


@unittest.skipUnless(BASH, 'Bash required')
class StorageGuards(unittest.TestCase):
    def setUp(self):
        (ROOT/'test-results').mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT/'test-results')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.bin = self.base / 'bin'
        self.bin.mkdir()
        self.mount = self.base / 'shared'
        self.mount.mkdir()
        config = self.base / 'storage.json'
        config.write_text(json.dumps(dict(file_system_id='fs-test', mount_target_ip='10.87.1.10', region='us-east-1', microvm_id='test-vm')))
        def posix(p):
            value = p.as_posix()
            return '/' + value[0].lower() + value[2:] if os.name == 'nt' else value
        self.env = dict(os.environ, STORAGE_CONFIG_FILE=posix(config), STORAGE_MOUNT=posix(self.mount))
        # Git Bash converts inherited PATH; inject POSIX syntax inside Bash.
        self.env['TEST_BIN'] = posix(self.bin)
        self.stub('sync', 'exit 0')

    def stub(self, name, body):
        p = self.bin / name
        p.write_text('#!/bin/bash\n' + body + '\n', newline='\n')
        p.chmod(0o755)

    def run_storage(self, *args):
        return subprocess.run([BASH, '-c', 'export PATH="$TEST_BIN:$PATH"; bash "$@"', 'test',
                               (ROOT/'01-microvms/bash/storage.sh').as_posix(), *args],
                              env=self.env, capture_output=True, text=True, timeout=10)

    def test_unmounted_directory_never_receives_file(self):
        self.stub('findmnt', 'exit 1')
        result = self.run_storage('write')
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn('refusing to write', result.stderr)
        self.assertEqual(list(self.mount.iterdir()), [])

    def test_local_filesystem_is_not_accepted_as_nfs(self):
        self.stub('findmnt', 'echo ext4')
        self.assertNotEqual(self.run_storage('write').returncode, 0)
        self.assertFalse((self.mount/'microvm-demo').exists())

    def test_failed_mount_does_not_report_success(self):
        self.stub('findmnt', 'exit 1')
        self.stub('mount', 'exit 32')
        result = self.run_storage('mount')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('mount failed', result.stderr)

    def test_write_and_read_have_matching_session_identity(self):
        self.stub('findmnt', 'echo nfs4')
        result = self.run_storage('write', 'validation-test.txt')
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = 'Written through NFS from MicroVM test-vm\n'
        self.assertEqual((self.mount/'microvm-demo/validation-test.txt').read_text(), expected)
        result = self.run_storage('read', 'validation-test.txt')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, expected)

    def test_reject_path_traversal(self):
        self.stub('findmnt', 'echo nfs4')
        self.assertNotEqual(self.run_storage('write', '../escape.txt').returncode, 0)
        self.assertFalse((self.mount/'escape.txt').exists())


if __name__ == '__main__':
    unittest.main()
