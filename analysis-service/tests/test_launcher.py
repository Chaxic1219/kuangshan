"""后台启动参数与重复启动保护。"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import launch_console


class LauncherTests(unittest.TestCase):
    def test_existing_console_does_not_spawn_again(self):
        with patch.object(launch_console, 'is_running', return_value=True), \
                patch.object(launch_console.subprocess, 'Popen') as spawn:
            launch_console.launch()
        spawn.assert_not_called()

    def test_launch_hidden_and_wait_for_ready(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(launch_console, 'ROOT', Path(tmp)), \
                patch.object(launch_console, 'is_running', side_effect=[False, False, True]), \
                patch.object(launch_console.subprocess, 'Popen') as spawn, \
                patch.object(launch_console.time, 'sleep'):
            spawn.return_value.poll.return_value = None
            launch_console.launch()
            kwargs = spawn.call_args.kwargs
            self.assertEqual(kwargs['creationflags'], launch_console.subprocess.CREATE_NO_WINDOW)
            self.assertEqual(kwargs['startupinfo'].wShowWindow, launch_console.subprocess.SW_HIDE)
            self.assertTrue(kwargs['startupinfo'].dwFlags & launch_console.subprocess.STARTF_USESHOWWINDOW)
            self.assertEqual(kwargs['stderr'], kwargs['stdout'])
            self.assertTrue((Path(tmp)/'data/console.log').exists())

    def test_start_failure_reports_log_location(self):
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(launch_console, 'ROOT', Path(tmp)), \
                patch.object(launch_console, 'is_running', return_value=False), \
                patch.object(launch_console.subprocess, 'Popen', return_value=Mock(poll=lambda: 1)):
            with self.assertRaisesRegex(RuntimeError, 'console.log'):
                launch_console.launch()
