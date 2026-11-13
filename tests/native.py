"""Black-box helpers for the native executable; never inherit provider secrets."""

from contextlib import contextmanager
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
SYSTEM_ENV = {name: os.environ[name] for name in (
    "PATH", "HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "SYSTEMROOT", "WINDIR",
    "COMSPEC", "PATHEXT", "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL",
) if name in os.environ}


def environment(settings=None):
    return {**SYSTEM_ENV, **(settings or {})}


def command():
    explicit = os.getenv("DECISION_TEST_EXECUTABLE")
    if explicit:
        return [str(Path(explicit).resolve())]
    suffix = ".exe" if os.name == "nt" else ""
    for directory in (ROOT / "target/debug", ROOT / "target/release", ROOT / ".state/rust-native/target/debug"):
        executable = directory / ("decision" + suffix)
        if executable.is_file():
            return [str(executable)]
    raise RuntimeError("Build decision with cargo, or set DECISION_TEST_EXECUTABLE")


def invoke(*args, data=None, env=None, cwd=None, timeout=30):
    return subprocess.run([*command(), *map(str, args)], input=data, env=environment(env),
                          cwd=cwd or os.getenv("DECISION_TEST_CWD"), text=True, encoding="utf-8",
                          capture_output=True, timeout=timeout)


@contextmanager
def response_server(response, status=200, delay=0):
    """Record the real provider request and serve a supplied wire response."""
    requests = []

    class Handler(BaseHTTPRequestHandler):
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
            requests.append({"path": self.path, "headers": dict(self.headers), "body": body, "uploads": uploads})
            if delay:
                time.sleep(delay)
            value = response(body) if callable(response) else response
            encoded = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            if 300 <= status < 400:
                self.send_header("Location", "/must-not-follow")
            self.end_headers()
            try:
                self.wfile.write(encoded)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05), daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def evaluate(provider, payload, response, *, status=200, flags=(), settings=None):
    with response_server(response, status=status) as (url, requests):
        env = {"DECISION_PROVIDER": provider, "DECISION_API_KEY_ENV": "FIXTURE_KEY", "FIXTURE_KEY": "fixture-key", **(settings or {})}
        result = invoke("run", "-", "--json", "--base-url", url, *flags,
                        data=json.dumps(payload, ensure_ascii=False), env=env)
    return result, requests
