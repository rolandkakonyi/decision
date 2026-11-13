"""Local behavioral regressions; never uses a real provider or credential."""
import asyncio
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import threading
import unittest
from unittest.mock import patch

import host
from store import Store
from wire import loads, operation

REQUEST = {"state": {"integer": 9007199254740993}, "questions": {"q": {"type": "noul", "instructions": "Is this present?"}}}
PLUGIN = '''import json,sys,os,time
request=json.loads(sys.stdin.readline())
method=request["method"]
if os.path.exists("hang"):
    open("pid", "w").write(str(os.getpid()))
    time.sleep(60)
if method=="describe": result={"name":"fixture","version":"1.0.0"}
elif method=="prepare": result=request["request"]
else:
    assert request["credential"]=="fixture-key"
    assert "DECISION_API_KEY" not in os.environ
    result={"model":"fixture","usage":{"input_tokens":0,"output_tokens":None},"answers":{"q":{"type":"noul","noul":0.75}}}
print(json.dumps({"protocol_version":1,"ok":True,"result":result}))
'''


class HostTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.package = self.root / "source"
        self.package.mkdir()
        (self.package / "provider.py").write_text(PLUGIN)
        self.metadata = {"protocol_version": 1, "name": "fixture", "version": "1.0.0", "command": [sys.executable, "provider.py"], "default_base_url": "https://example.test", "credential_env_vars": ["FIXTURE_KEY"]}
        (self.package / "decision-provider.json").write_text(json.dumps(self.metadata))
        self.store = Store(self.root / "home")
        self.store.install(str(self.package))
        self.env = patch.dict(os.environ, {"DECISION_API_KEY": "fixture-key"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def make_host(self, *options):
        args = host.parser().parse_args(["run", "-", "--plugin-home", str(self.store.home), *options])
        return host.Host(args)

    async def test_external_roundtrip_and_raw_integer(self):
        selected = self.make_host("--provider", "fixture")
        await selected.initialize()
        prepared, _ = await selected.prepare(REQUEST)
        self.assertEqual(prepared, REQUEST)
        result = await selected.evaluate(REQUEST)
        self.assertEqual(result["usage"], {"input_tokens": 0, "output_tokens": None})
        self.assertEqual(result["answers"]["q"]["noul"], 0.75)
        self.assertGreaterEqual(result["duration_ms"], 0)

    async def test_disable_precedes_launch(self):
        root, _ = self.store.resolve("fixture")
        (root / "provider.py").write_text('raise RuntimeError("must not launch")')
        selected = self.make_host("--provider", "fixture", "--disable-provider", "fixture")
        with self.assertRaisesRegex(ValueError, "disabled"):
            await selected.initialize()

    async def test_snapshot_survives_remove_and_update(self):
        selected = self.make_host("--provider", "fixture")
        await selected.initialize()
        self.store.remove("fixture")
        (self.package / "provider.py").write_text('raise RuntimeError("updated")')
        self.store.install(str(self.package))
        self.assertEqual((await selected.evaluate(REQUEST))["model"], "fixture")

    async def test_timeout_and_cancel_reap_process(self):
        root, metadata = self.store.resolve("fixture")
        (root / "hang").touch()
        with self.assertRaises(host.ProviderError):
            await operation(root, metadata, "describe", 0.1)
        pid = int((root / "pid").read_text())
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        task = asyncio.create_task(operation(root, metadata, "describe", 10))
        for _ in range(100):
            await asyncio.sleep(0.01)
            new_pid = int((root / "pid").read_text())
            if new_pid != pid:
                break
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        with self.assertRaises(ProcessLookupError):
            os.kill(new_pid, 0)

    async def test_failed_setup_preserves_registration(self):
        previous = self.store.registrations()
        self.metadata["setup"] = [sys.executable, "-c", "raise SystemExit(1)"]
        (self.package / "decision-provider.json").write_text(json.dumps(self.metadata))
        with self.assertRaises(host.ProviderError):
            self.store.install(str(self.package))
        self.assertEqual(self.store.registrations(), previous)

    async def test_setup_runs_at_permanent_artifact_path(self):
        (self.package / "setup.py").write_text("from pathlib import Path\nPath('install-path').write_text(str(Path.cwd()))\n")
        self.metadata["setup"] = [sys.executable, "setup.py"]
        (self.package / "decision-provider.json").write_text(json.dumps(self.metadata))
        self.store.install(str(self.package))
        root, _ = self.store.resolve("fixture")
        self.assertEqual((root / "install-path").read_text(), str(root))

    async def test_git_install_records_commit_and_detaches_source(self):
        for command in (["git", "init", "-q"], ["git", "add", "."],
                        ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", "commit", "-qm", "Fixture"]):
            subprocess.run(command, cwd=self.package, check=True, capture_output=True)
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.package, text=True).strip()
        self.store.install("git+" + self.package.as_uri() + "#" + commit)
        self.assertEqual(self.store.registrations()["fixture"]["commit"], commit)
        root, _ = self.store.resolve("fixture")
        self.assertFalse((root / ".git").exists())

    async def test_archive_hash_and_unsafe_members_preserve_registration(self):
        previous = self.store.registrations()
        archive = self.root / "fixture.tar.gz"
        with tarfile.open(archive, "w:gz") as output:
            for path in self.package.iterdir():
                output.add(path, arcname=path.name)
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                data = archive.read_bytes()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            def log_message(self, *_):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        source = f"http://127.0.0.1:{server.server_port}/fixture.tar.gz"
        with self.assertRaises(ValueError):
            self.store.install(source, "0" * 64)
        self.assertEqual(self.store.registrations(), previous)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        self.assertEqual(self.store.install(source, digest)["name"], "fixture")
        previous = self.store.registrations()
        for name, kind in (("../escape", tarfile.REGTYPE), ("link", tarfile.SYMTYPE), ("hard", tarfile.LNKTYPE)):
            with tarfile.open(archive, "w:gz") as output:
                member = tarfile.TarInfo(name)
                member.type = kind
                member.linkname = "../escape"
                output.addfile(member)
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            with self.assertRaises(ValueError):
                self.store.install(source, digest)
            self.assertEqual(self.store.registrations(), previous)

    async def test_explicit_project_overrides_only_when_selected(self):
        project = self.root / "project.json"
        project.write_text('{"providers":{}}')
        scoped = Store(self.store.home, project)
        scoped.install(str(self.package))
        self.assertNotEqual(scoped.resolve("fixture")[0], self.store.resolve("fixture")[0])
        self.assertEqual(len(self.store.listing()), 1)

    async def test_typesafe_raw_validation_and_no_redirects(self):
        seen = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                request = loads(self.rfile.read(int(self.headers["Content-Length"])))
                seen.append(request)
                state = request["state"]
                if state == "redirect":
                    self.send_response(307)
                    self.send_header("Location", "/redirected")
                    self.end_headers()
                    return
                answer = True if state == "bool" else 0.75
                result = {"model": "fixture", "answers": {"q": {"type": "noul", "noul": answer}}, "usage": {"input_tokens": 0}}
                raw = json.dumps(result).encode()
                if state == "duplicate":
                    raw = raw.replace(b'"noul": 0.75', b'"noul": 0.5, "noul": 0.75')
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            def log_message(self, *_):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        selected = self.make_host("--base-url", f"http://127.0.0.1:{server.server_port}")
        await selected.initialize()
        result = await selected.evaluate(REQUEST)
        self.assertEqual(result["usage"], {"input_tokens": 0, "output_tokens": None})
        self.assertEqual(seen[0]["state"]["integer"], 9007199254740993)
        for state in ("bool", "duplicate", "redirect"):
            with self.assertRaises(host.ProviderError):
                await selected.evaluate(dict(REQUEST, state=state))
        self.assertEqual(len(seen), 4)

    async def test_wire_rejects_duplicates_and_nonfinite(self):
        for text in ('{"a":1,"a":2}', '{"a":NaN}', '{"a":1e999}'):
            with self.assertRaises(ValueError):
                loads(text)
        for invalid in (True, "0.5", -0.1, 1.1):
            from decision_cli.results import validate_result
            with self.assertRaises(host.ProviderError):
                validate_result({"model": "fixture", "usage": {}, "answers": {"q": {"type": "noul", "noul": invalid}}}, REQUEST["questions"])


if __name__ == "__main__":
    unittest.main()
