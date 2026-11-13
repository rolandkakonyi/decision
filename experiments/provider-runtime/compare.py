"""Collect matched, alternating measurements of the built prototype artifacts."""

import argparse
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import tempfile
import time

from fixtures import MCP, REQUEST, canonical_result, environment, image_request, package, provider_server


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def tree_rss(pid):
    """Snapshot RSS sum; shared pages can be counted more than once."""
    text = subprocess.check_output(["ps", "-axo", "pid=,ppid=,rss="], text=True)
    rows = [tuple(map(int, line.split())) for line in text.splitlines() if line.strip()]
    included = {pid}
    while True:
        children = {child for child, parent, _ in rows if parent in included}
        expanded = included | children
        if included == expanded:
            break
        included = expanded
    selected = [(child, rss) for child, _, rss in rows if child in included]
    return {"rss_sum_bytes": sum(rss for _, rss in selected) * 1024, "process_count": len(selected)}


def artifact(binary):
    data = binary.read_bytes()
    return {"sha256": digest(data), "executable_bytes": len(data),
            "gzip_executable_bytes": len(gzip.compress(data, mtime=0))}


def run_cli(binary, arguments, payload=None, env=None):
    started = time.perf_counter_ns()
    process = subprocess.run([str(binary), *map(str, arguments)],
                             input=None if payload is None else json.dumps(payload),
                             capture_output=True, text=True, env=env or environment(), timeout=40)
    seconds = (time.perf_counter_ns() - started) / 1e9
    if process.returncode:
        raise RuntimeError(f"{binary.name} failed: {process.stderr[:300]}")
    if arguments == ["--version"]:
        if process.stdout != "decision prototype 1\n":
            raise ValueError("prototype version output differs")
        result = process.stdout
    else:
        result = json.loads(process.stdout)
    return seconds, result, None


def measure(binary, name, root, url):
    home = root / binary.parent.name / "plugins"
    # Artifact paths can share a parent name; variant-specific root is supplied.
    common = ["--plugin-home", str(home)]
    if name == "version":
        return run_cli(binary, ["--version"])
    if name == "dry_run":
        env = {key: value for key, value in environment().items() if key != "FIXTURE_KEY"}
        return run_cli(binary, ["run", "-", "--json", "--dry-run", *common], REQUEST, env)
    if name == "mcp_ready":
        started = time.perf_counter_ns()
        with MCP([str(binary), "mcp", "--base-url", url, *common]) as client:
            tools = client.request("tools/list", {})
            seconds = (time.perf_counter_ns() - started) / 1e9
            # Compare the operational MCP contract; preserve full responses too.
            canonical = {"protocolVersion": client.initialization["protocolVersion"],
                         "tools": [{key: tool[key] for key in ("name", "inputSchema", "outputSchema")}
                                   for tool in tools["tools"]]}
            memory = tree_rss(client.process.pid)
            return seconds, {"canonical": canonical, "raw_initialize": client.initialization, "raw_tools": tools}, memory
    payload = image_request() if name == "typesafe_images" else REQUEST
    provider = "fixture" if name == "external_plugin" else "typesafe"
    return run_cli(binary, ["run", "-", "--json", "--provider", provider, "--base-url", url, *common], payload)


def canonical(name, result):
    if name == "mcp_ready":
        return result["canonical"]
    return canonical_result(result) if isinstance(result, dict) else result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python-binary", type=Path, required=True)
    parser.add_argument("--go-binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runs", type=int, default=7)
    parser.add_argument("--warmups", type=int, default=2)
    parser.add_argument("--evaluator", type=Path, required=True,
                        help="benchmark-change skill's scripts/benchmark_change.py")
    args = parser.parse_args()
    if args.runs < 5 or args.warmups < 1:
        parser.error("require at least five measured runs and one warmup per variant")
    args.output.mkdir(parents=True, exist_ok=True)
    variants = {"baseline": args.python_binary.resolve(), "candidate": args.go_binary.resolve()}
    evaluator_spec = importlib.util.spec_from_file_location("benchmark_evaluator", args.evaluator)
    evaluator = importlib.util.module_from_spec(evaluator_spec)
    evaluator_spec.loader.exec_module(evaluator)
    report = {"variants": {name: artifact(binary) for name, binary in variants.items()},
              "environment": {"os": platform.platform(), "architecture": platform.machine(),
                              "cpu_count": os.cpu_count(), "python": platform.python_version(),
                              "load_average_start": os.getloadavg(),
                              "go": subprocess.check_output(["go", "version"], text=True).strip()},
              "method": {"runs_per_variant": args.runs, "warmups_per_variant": args.warmups,
                         "host_label": "local-macos-arm64", "baseline": "Python frozen prototype",
                         "candidate": "Go native prototype",
                         "memory": "MCP-ready process-tree RSS snapshot sum, not peak; shared pages can be counted twice",
                         "canonicalization": "Remove duration_ms; normalize integral numeric answer formatting; usage unchanged. MCP projection includes negotiated version and full tool names/input/output schemas; raw metadata retained.",
                         "cold_cache": "Not controlled; first observations are already acceptance-exercised artifacts and are not cold-cache claims"},
              "scenarios": {}}
    names = ("version", "dry_run", "mcp_ready", "typesafe_text", "typesafe_images", "external_plugin")
    with tempfile.TemporaryDirectory(prefix="decision-comparison-") as folder, provider_server() as (url, requests):
        root = Path(folder)
        for variant, binary in variants.items():
            directory = root / variant
            source = package(directory / "source")
            home = directory / binary.parent.name / "plugins"
            run_cli(binary, ["providers", "install", str(source), "--plugin-home", str(home)])
        for name in names:
            manifest = {"metric": "seconds", "direction": "lower",
                        "warmups": {key: args.warmups for key in variants}, "runs": []}
            first = {}
            warmups = []
            rss = {key: [] for key in variants}
            for variant, binary in variants.items():
                seconds, result, memory = measure(binary, name, root / variant, url)
                first[variant] = {"value": seconds, "output_hash": digest(encoded(canonical(name, result)))}
            for _ in range(args.warmups):
                for variant, binary in variants.items():
                    seconds, result, memory = measure(binary, name, root / variant, url)
                    warmups.append({"variant": variant, "value": seconds,
                                    "output_hash": digest(encoded(canonical(name, result)))})
            for attempt in range(args.runs):
                for variant, binary in variants.items():
                    seconds, result, memory = measure(binary, name, root / variant, url)
                    sequence = len(manifest["runs"]) + 1
                    output = args.output / "outputs" / name / f"{sequence:02}-{variant}.json"
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_bytes(encoded(result) + b"\n")
                    if memory:
                        rss[variant].append(memory)
                    manifest["runs"].append({"sequence": sequence, "variant": variant, "value": seconds,
                                              "output_hash": digest(encoded(canonical(name, result))),
                                              "attempt_id": f"{name}-{attempt + 1}-{variant}",
                                              "host": "local-macos-arm64", "context": "same-host-serial-built-artifacts-loopback-fixture",
                                              "raw_output": str(output.relative_to(args.output))})
            evaluation = evaluator.evaluate(manifest, minimum_runs=5)
            scenario = {"first_uncontrolled_observations": first, "warmups": warmups,
                        "manifest": manifest, "evaluation": evaluation}
            if any(rss.values()):
                scenario["mcp_ready_memory"] = {
                    variant: {"median_rss_sum_bytes": statistics.median(item["rss_sum_bytes"] for item in samples),
                              "samples": samples} for variant, samples in rss.items()}
            report["scenarios"][name] = scenario
            (args.output / f"{name}.manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
            print(json.dumps({"scenario": name, **evaluation}), flush=True)
        report["fixture_request_count"] = len(requests)
    report["environment"]["load_average_end"] = os.getloadavg()
    (args.output / "results.json").write_text(json.dumps(report, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
