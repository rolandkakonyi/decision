"""Bounded Codex app-server client for isolated native plugin discovery tests."""

import json
import os
import queue
import signal
import subprocess
import threading
import time


class CodexClient:
    def __init__(self, executable, cwd, environment):
        self.command = [executable] if isinstance(executable, str) else list(executable)
        self.cwd, self.environment = cwd, environment

    def __enter__(self):
        self.process = subprocess.Popen([*self.command, "app-server"], cwd=self.cwd, env=self.environment,
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                        text=True, encoding="utf-8", start_new_session=os.name == "posix")
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
        deadline = time.monotonic() + 30
        while True:
            message = self.messages.get(timeout=max(0, deadline - time.monotonic()))
            if isinstance(message, Exception):
                raise message
            if message is None:
                raise RuntimeError("Codex app-server exited during plugin validation")
            if message.get("id") == self.sequence:
                if "error" in message:
                    raise RuntimeError(str(message["error"]))
                return message["result"]

    def __exit__(self, *exc):
        if os.name == "posix":
            self.signal_group(signal.SIGTERM)
        else:
            # Kill the tree before its launcher exits and loses child ancestry.
            subprocess.run(["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
                           env=self.environment, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, check=False, timeout=15)
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        finally:
            # Git clones can outlive both the npm launcher and app-server.
            if os.name == "posix":
                self.signal_group(signal.SIGKILL)
            elif self.process.poll() is None:
                self.process.kill()
            self.process.wait(timeout=10)
        self.reader.join(timeout=5)
        self.process.stdin.close()
        self.process.stdout.close()

    def signal_group(self, sig):
        try:
            os.killpg(self.process.pid, sig)
        except ProcessLookupError:
            pass
