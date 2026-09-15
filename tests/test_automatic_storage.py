"""Background mount must not block launch or create duplicate mount jobs."""
import importlib.util
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location('guest', Path(__file__).resolve().parents[1] / '01-microvms/bash/server.py')
guest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guest)


class AutomaticStorage(unittest.TestCase):
    def lab(self):
        lab = guest.Lab.__new__(guest.Lab)
        lab.storage = {'state': 'disabled'}
        lab.storage_lock = threading.Lock()
        return lab

    def wait_done(self, lab):
        until = time.monotonic() + 2
        while lab.storage['state'] == 'connecting' and time.monotonic() < until:
            time.sleep(.01)
        self.assertNotEqual(lab.storage['state'], 'connecting')

    def test_mount_returns_before_completion_and_deduplicates(self):
        lab = self.lab()
        release = threading.Event()
        entered = threading.Event()
        def mount(*args, **kwargs):
            entered.set()
            release.wait(2)
            return SimpleNamespace(returncode=0)
        with patch.object(guest.subprocess, 'run', side_effect=mount) as run:
            lab.start_storage('mount')
            self.assertTrue(entered.wait(1))
            self.assertEqual(lab.storage['state'], 'connecting')
            lab.start_storage('mount')
            self.assertEqual(run.call_count, 1)
            release.set()
            self.wait_done(lab)
        self.assertEqual(lab.storage['state'], 'ready')

    def test_mount_failure_is_visible(self):
        lab = self.lab()
        with patch.object(guest.subprocess, 'run', return_value=SimpleNamespace(returncode=32)):
            lab.start_storage('mount')
            self.wait_done(lab)
        self.assertEqual(lab.storage['state'], 'error')

    def test_resume_checks_without_remounting(self):
        lab = self.lab()
        lab.storage = {'state': 'ready'}
        lab.events, lab.ticks = [], 0
        with patch.object(lab, 'start_storage') as start:
            self.assertEqual(lab.hook('resume', {}), {'ok': True})
            start.assert_called_once_with('check')


if __name__ == '__main__':
    unittest.main()
