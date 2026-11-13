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


@unittest.skipUnless(os.name == "posix", "process-group teardown is used on Linux/macOS")
class CodexClientTests(unittest.TestCase):
    def assert_writer_stopped(self, pid):
        deadline = time.monotonic() + 2
        while True:
            result = subprocess.run(["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True, check=False)
            state = result.stdout.strip()
            if not state or state.startswith("Z"):
                return
            if time.monotonic() >= deadline:
                self.fail("Codex teardown left a background writer running")
            time.sleep(.01)

    def test_stops_orphan_writer_after_normal_exit_and_initialization_failure(self):
        for fail in (False, True):
            with self.subTest(initialization_failure=fail), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "writer.py").write_text(WRITER)
                server = root / "server"
                server.write_text(f"#!{sys.executable}\n" + SERVER)
                server.chmod(0o700)
                if fail:
                    (root / "fail").touch()
                try:
                    if fail:
                        with self.assertRaisesRegex(RuntimeError, "Fixture initialization failed"):
                            with CodexClient(str(server), root, dict(os.environ)):
                                self.fail("Initialization should fail")
                    else:
                        with CodexClient(str(server), root, dict(os.environ)) as client:
                            client.call("fixture", {})
                    self.assert_writer_stopped(int((root / "writer.pid").read_text()))
                finally:
                    # Keep the regression test leak-free when run against the broken helper.
                    if (root / "writer.pid").exists():
                        try:
                            os.kill(int((root / "writer.pid").read_text()), signal.SIGKILL)
                        except ProcessLookupError:
                            pass


if __name__ == "__main__":
    unittest.main()
