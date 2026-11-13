"""Shared local HTTP, package, and MCP fixtures for the prototype comparison."""

import base64
from contextlib import contextmanager
from email.parser import BytesParser
from email.policy import default
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import tarfile
import threading
import time

from plugin_fixture import answers_for

HERE = Path(__file__).resolve().parent
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a6y8AAAAASUVORK5CYII=")
REQUEST = {"state": {"message": "red square", "large": 9007199254740993}, "model": "fixture-1",
           "questions": {"red": {"type": "noul", "instructions": "Is <photo> red?"},
                         "team": {"type": "choice", "instructions": "Choose a team", "criteria": {"billing": "Payments", "technical": "Bugs"}},
                         "blocking": {"type": "score", "instructions": "Assess severity", "criteria": ["Low", "High"]},
                         "location": {"type": "box", "instructions": "Locate <photo>"}}}


def image_request():
    return {**REQUEST, "assets": {"<photo>": {"data_base64": base64.b64encode(PNG).decode(), "format": "png"}}}


def environment():
    # Never inherit real credentials into a fixture process.
    result = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL") if key in os.environ}
    result.update(DECISION_API_KEY_ENV="FIXTURE_KEY", FIXTURE_KEY="fixture-only-key",
                  DECISION_MODEL="fixture-1", UNRELATED_SECRET="must-not-reach-plugin")
    return result


def package(directory, version="1.0.0", marker=None, setup_fails=False):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(HERE / "plugin_fixture.py", directory / "provider.py")
    command = [sys.executable, "provider.py"]
    if marker:
        command.append(str(marker))
    manifest = {"protocol_version": 1, "name": "fixture", "version": version,
                "command": command, "setup": [sys.executable, "setup.py"],
                "default_base_url": "https://api.example.test", "default_model": "fixture-" + version,
                "credential_env_vars": ["FIXTURE_API_KEY"], "schema_constraints": {}}
    (directory / "decision-provider.json").write_text(json.dumps(manifest))
    setup = "from pathlib import Path\nPath('setup-ran').write_text('ready')\n"
    if setup_fails:
        setup += "raise SystemExit(9)\n"
    (directory / "setup.py").write_text(setup)
    return directory


def archive(source, destination):
    with tarfile.open(destination, "w:gz") as output:
        for item in Path(source).iterdir():
            output.add(item, arcname=item.name)
    return hashlib.sha256(Path(destination).read_bytes()).hexdigest()


@contextmanager
def provider_server(archive_directory=None):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = Path(archive_directory or "/nonexistent") / self.path.rsplit("/", 1)[-1]
            if not path.is_file():
                self.send_error(404)
                return
            data = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            uploads = {}
            if self.headers.get("Content-Type", "").startswith("multipart/form-data"):
                message = BytesParser(policy=default).parsebytes(("Content-Type: " + self.headers["Content-Type"] + "\r\n\r\n").encode() + raw)
                parts = {part.get_param("name", header="content-disposition"): part for part in message.iter_parts()}
                body = json.loads(parts.pop("request").get_payload(decode=True))
                uploads = {name: part.get_payload(decode=True) for name, part in parts.items()}
            else:
                body = json.loads(raw)
            requests.append({"path": self.path, "auth": self.headers.get("Authorization"),
                             "opt_in": self.headers.get("TypeSafe-Danger-Zone-Images"), "body": body, "uploads": uploads})
            state = body.get("state")
            mode = state.get("fixture_mode", "normal") if isinstance(state, dict) else state
            result = {"model": body.get("model", "fixture-1"), "answers": answers_for(body),
                      "usage": {"input_tokens": 0, "output_tokens": None}}
            if mode == "missing_answer":
                result["answers"].pop(next(iter(result["answers"])))
            if mode == "negative_usage":
                result["usage"]["input_tokens"] = -1
            if mode == "float_usage":
                result["usage"]["input_tokens"] = 5.0
            if mode == "bool_answer":
                result["answers"]["red"]["noul"] = True
            if mode == "redirect":
                self.send_response(307)
                self.send_header("Location", "/redirect-target")
                self.end_headers()
                return
            failure = mode == "provider_error"
            self.send_response(503 if failure else 200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            data = json.dumps({"error": "fixture-only-key provider-private-body"} if failure else result).encode()
            try:
                self.wfile.write(data)
            except BrokenPipeError:
                pass

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class MCP:
    """Wire-level client keeps acceptance independent of either host SDK."""

    def __init__(self, command, env=None):
        self.command, self.env = command, env or environment()
        self.sequence = 0
        self.messages = queue.Queue()
        self.pending = {}
        self.stderr = bytearray()

    def __enter__(self):
        self.process = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, env=self.env)
        def read():
            for line in self.process.stdout:
                try:
                    self.messages.put(json.loads(line))
                except ValueError:
                    self.messages.put({"invalid_protocol": True})
            self.messages.put({"eof": True})
        def diagnostics():
            while data := self.process.stderr.read(4096):
                if len(self.stderr) < 65536:
                    self.stderr.extend(data[:65536 - len(self.stderr)])
        self.read_thread = threading.Thread(target=read, daemon=True)
        self.err_thread = threading.Thread(target=diagnostics, daemon=True)
        self.read_thread.start()
        self.err_thread.start()
        try:
            self.initialization = self.request("initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                                               "clientInfo": {"name": "prototype-comparison", "version": "1"}})
            self.notify("notifications/initialized", {})
        except BaseException:
            self.__exit__()
            raise
        return self

    def send(self, message):
        self.process.stdin.write((json.dumps({"jsonrpc": "2.0", **message}) + "\n").encode())
        self.process.stdin.flush()

    def notify(self, method, params):
        self.send({"method": method, "params": params})

    def start(self, method, params):
        self.sequence += 1
        self.send({"id": self.sequence, "method": method, "params": params})
        return self.sequence

    def finish(self, identifier, timeout=30):
        if identifier in self.pending:
            return self.pending.pop(identifier)
        deadline = time.monotonic() + timeout
        while True:
            message = self.messages.get(timeout=max(0.001, deadline - time.monotonic()))
            if message.get("eof") or message.get("invalid_protocol"):
                raise AssertionError(f"MCP ended or emitted non-protocol output: {self.stderr.decode(errors='replace')[:200]}")
            if message.get("id") == identifier:
                if "error" in message:
                    raise AssertionError(f"MCP error: {message['error']}")
                return message.get("result")
            if "id" in message:
                self.pending[message["id"]] = message

    def request(self, method, params):
        return self.finish(self.start(method, params))

    def call(self, name, arguments):
        return self.request("tools/call", {"name": name, "arguments": arguments})

    def __exit__(self, *_):
        self.process.stdin.close()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.read_thread.join(timeout=2)
        self.err_thread.join(timeout=2)
        self.process.stdout.close()
        self.process.stderr.close()


def structured(result):
    if "structuredContent" in result:
        return result["structuredContent"]
    return json.loads(result["content"][0]["text"])


def canonical_result(result):
    result = json.loads(json.dumps(result))
    result.pop("duration_ms", None)
    # JSON numeric formatting (0 versus 0.0) is immaterial for answer numbers.
    # Token-count types are deliberately not normalized; rejection is tested.
    def numbers(value):
        if isinstance(value, dict):
            return {key: numbers(item) for key, item in value.items()}
        if isinstance(value, list):
            return [numbers(item) for item in value]
        return int(value) if type(value) is float and value.is_integer() else value
    if "answers" in result:
        result["answers"] = numbers(result["answers"])
    return result
