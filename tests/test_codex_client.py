"""Codex teardown stops background writers before temporary-home cleanup."""

import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from codex_client import CodexClient
from native import environment

WRITER = """
import os
from pathlib import Path
import signal
import sys
import time

root = Path(sys.argv[1])
signal.signal(signal.SIGTERM, signal.SIG_IGN)
(root / 'writer.pid').write_text(str(os.getpid()))
while True:
    clone = root / 'plugins-clone' / 'plugins'
    clone.mkdir(parents=True, exist_ok=True)
    (clone / 'progress').write_text(str(time.monotonic()))
    time.sleep(.01)
"""

SERVER = """
import json
from pathlib import Path
import subprocess
import sys
import time

root = Path.cwd()
subprocess.Popen([sys.executable, str(root / 'writer.py'), str(root)],
                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
deadline = time.monotonic() + 5
while not (root / 'writer.pid').exists():
    if time.monotonic() > deadline:
        raise RuntimeError('Writer did not start')
    time.sleep(.01)
for line in sys.stdin:
    request = json.loads(line)
    if 'id' in request:
        result = {'error': {'message': 'Fixture initialization failed'}} if (root / 'fail').exists() else {'result': {}}
        print(json.dumps({'id': request['id'], **result}), flush=True)
"""


def running(pid):
    if os.name != "nt":
        result = subprocess.run(["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True, check=False)
        state = result.stdout.strip()
        return bool(state) and not state.startswith("Z")
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        return code.value == 259
    finally:
        kernel.CloseHandle(handle)


def stop(pid):
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False, timeout=15)
    else:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


class CodexClientTests(unittest.TestCase):
    def assert_writer_stopped(self, pid):
        deadline = time.monotonic() + 2
        while running(pid):
            if time.monotonic() >= deadline:
                self.fail("Codex teardown left a background writer running")
            time.sleep(.01)

    def test_stops_orphan_writer_after_normal_exit_and_initialization_failure(self):
        for fail in (False, True):
            with self.subTest(initialization_failure=fail), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "writer.py").write_text(WRITER, encoding="utf-8")
                server = root / "server.py"
                server.write_text(SERVER, encoding="utf-8")
                if fail:
                    (root / "fail").touch()
                try:
                    command = [sys.executable, str(server)]
                    if fail:
                        with self.assertRaisesRegex(RuntimeError, "Fixture initialization failed"):
                            with CodexClient(command, root, environment()):
                                self.fail("Initialization should fail")
                    else:
                        with CodexClient(command, root, environment()) as client:
                            client.call("fixture", {})
                    self.assert_writer_stopped(int((root / "writer.pid").read_text()))
                finally:
                    # Keep this regression leak-free against a broken helper.
                    if (root / "writer.pid").exists():
                        pid = int((root / "writer.pid").read_text())
                        if running(pid):
                            stop(pid)


if __name__ == "__main__":
    unittest.main()
