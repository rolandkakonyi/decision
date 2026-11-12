"""Bounded Codex app-server client for isolated plugin discovery tests."""

import json
import queue
import subprocess
import threading


class CodexClient:
    def __init__(self, executable, cwd, environment):
        self.executable, self.cwd, self.environment = executable, cwd, environment

    def __enter__(self):
        self.process = subprocess.Popen([self.executable, "app-server"], cwd=self.cwd, env=self.environment,
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        self.messages = queue.Queue()
        self.sequence = 0
        self.reader = threading.Thread(target=self.read, daemon=True)
        self.reader.start()
        try:
            self.call("initialize", {"clientInfo": {"name": "decision-plugin-test", "version": "1"},
                                     "capabilities": {"experimentalApi": True}})
            self.send({"method": "initialized", "params": {}})
        except BaseException:
            self.__exit__()
            raise
        return self

    def read(self):
        try:
            for line in self.process.stdout:
                self.messages.put(json.loads(line))
        except (OSError, ValueError) as error:
            self.messages.put(error)
        finally:
            self.messages.put(None)

    def send(self, message):
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def call(self, method, params):
        self.sequence += 1
        self.send({"id": self.sequence, "method": method, "params": params})
        while True:
            message = self.messages.get(timeout=30)
            if isinstance(message, Exception):
                raise message
            if message is None:
                raise RuntimeError("Codex app-server exited during plugin validation")
            if message.get("id") == self.sequence:
                if "error" in message:
                    raise RuntimeError(str(message["error"]))
                return message["result"]

    def __exit__(self, *exc):
        self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        self.reader.join(timeout=5)
        self.process.stdin.close()
        self.process.stdout.close()
