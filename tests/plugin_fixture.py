"""Dependency-free executable provider fixture."""

import json
import os
from pathlib import Path
import subprocess
import sys
import time


def unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate key")
        value[key] = item
    return value


def answers_for(request):
    answers = {}
    for name, question in request["questions"].items():
        kind = question["type"]
        if kind == "noul":
            answer = {"type": kind, "noul": 0.9}
        elif kind == "choice":
            choices = list(question["criteria"])
            answer = {"type": kind, "choice": choices[0], "confidence": 1.0,
                      "probabilities": {key: float(key == choices[0]) for key in choices}}
        elif kind == "score":
            levels = question["criteria"]
            answer = {"type": kind, "score": 0.0, "confidence": 1.0,
                      "legend": {str(i): value for i, value in enumerate(levels)},
                      "probabilities": {str(i): float(i == 0) for i in range(len(levels))}}
        else:
            answer = {"type": kind, "box": {"left": 0.1, "top": 0.2, "right": 0.8, "bottom": 0.9},
                      "confidence": 0.8}
        answers[name] = answer
    return answers


def trace(path, record):
    if path:
        with Path(path).open("a") as output:
            output.write(json.dumps(record) + "\n")


def main():
    manifest = json.loads(Path("decision-provider.json").read_text())
    message = json.loads(sys.stdin.buffer.readline(48 * 1024 * 1024 + 1), object_pairs_hook=unique)
    method = message["method"]
    request = message.get("request", {})
    state = request.get("state", {})
    mode = state.get("fixture_mode", "normal") if isinstance(state, dict) else "normal"
    marker = sys.argv[1] if len(sys.argv) > 1 else None
    trace(marker, {"method": method, "pid": os.getpid(),
                   "integer": state.get("large") if isinstance(state, dict) else None})
    error = None
    if method == "describe":
        result = {"name": manifest["name"], "version": manifest["version"]}
    elif method == "prepare":
        result = request
        if mode == "invalid_prepare":
            error = "invalid_request"
        if mode == "mutated_prepare":
            result["state"] = "changed by plugin"
    elif method == "evaluate":
        if message.get("credential") != "fixture-only-key" or "UNRELATED_SECRET" in os.environ:
            error = "provider_error"
        if mode == "hang":
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
            trace(marker, {"child_pid": child.pid})
            time.sleep(60)
        result = {"model": request.get("model", manifest["default_model"]),
                  "answers": answers_for(request), "usage": {"input_tokens": 0, "output_tokens": None}}
        first = next(iter(result["answers"]))
        if mode == "version":
            for answer in result["answers"].values():
                if answer["type"] == "noul":
                    answer["noul"] = 0.1 if manifest["version"].startswith("1.") else 0.2
        if mode == "missing_answer":
            result["answers"].pop(first)
        elif mode == "wrong_type":
            result["answers"][first] = {"type": "score", "score": 0}
        elif mode == "negative_usage":
            result["usage"]["input_tokens"] = -1
        elif mode == "float_usage":
            result["usage"]["input_tokens"] = 5.0
        elif mode == "bool_answer":
            result["answers"][first]["noul"] = True
        elif mode == "leak_error":
            print("fixture-only-key provider-private-body", file=sys.stderr)
            error = "provider_error"
        elif mode == "malformed_output":
            print("fixture-only-key provider-private-body")
            return
    else:
        error, result = "invalid_request", None
    if error:
        response = {"protocol_version": 1, "ok": False,
                    "error": {"code": error, "message": "fixture-only-key provider-private-body"}}
    else:
        response = {"protocol_version": 1, "ok": True, "result": result}
    encoded = json.dumps(response, ensure_ascii=False, allow_nan=False)
    if mode == "duplicate_output" and method == "evaluate":
        encoded = encoded.replace('"protocol_version": 1', '"protocol_version": 1, "protocol_version": 1', 1)
    print(encoded, flush=True)


if __name__ == "__main__":
    main()
