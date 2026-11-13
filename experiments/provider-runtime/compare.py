"""Collect matched, alternating measurements of the built prototype artifacts."""

import argparse
from contextlib import ExitStack
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import platform
import shutil
import statistics
import subprocess
import tarfile
import tempfile
import time

from fixtures import MCP, REQUEST, canonical_result, environment, image_request, package, provider_server, structured

SCENARIOS = ("version", "dry_run", "mcp_ready", "typesafe_text", "typesafe_images", "external_plugin",
             "mcp_typesafe_text", "mcp_typesafe_images", "mcp_external_plugin")


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


def artifact(binary, distribution):
    data = binary.read_bytes()
    files = {"decision": data, "LICENSE": (Path(__file__).resolve().parents[2] / "LICENSE").read_bytes()}
    if (binary.parent / "THIRD_PARTY_NOTICES.txt").is_file():
        files["THIRD_PARTY_NOTICES.txt"] = (binary.parent / "THIRD_PARTY_NOTICES.txt").read_bytes()
    else:
        for path in sorted((binary.parent / "licenses").rglob("*")):
            if path.is_file():
                files["licenses/" + str(path.relative_to(binary.parent / "licenses"))] = path.read_bytes()
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w") as output:
        for name, content in sorted(files.items()):
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(content), 0o755 if name == "decision" else 0o644, 0
            output.addfile(info, io.BytesIO(content))
    packed = gzip.compress(archive.getvalue(), mtime=0)
    distribution.parent.mkdir(parents=True, exist_ok=True)
    distribution.write_bytes(packed)
    return {"sha256": digest(data), "executable_bytes": len(data),
            "gzip_executable_bytes": len(gzip.compress(data, mtime=0)),
            "distribution_bytes": len(packed), "distribution_sha256": digest(packed)}


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


def descriptive_evaluation(manifest, minimum_runs=5):
    """Portable collector mode reports observations without a performance gate."""
    samples = {variant: [row["value"] for row in manifest["runs"] if row["variant"] == variant]
               for variant in ("baseline", "candidate")}
    stats = {variant: {"count": len(values), "median": statistics.median(values),
                       "min": min(values), "max": max(values)} for variant, values in samples.items()}
    baseline = stats["baseline"]["median"]
    stats["improvement_percent"] = (baseline - stats["candidate"]["median"]) / baseline * 100
    return {"claimable": None, "metric": manifest["metric"], "direction": manifest["direction"],
            "statistics": stats, "reasons": ["Descriptive measurements; no external evidence gate requested"]}


def collect(name, variants, args, evaluate, invoke):
    manifest = {"metric": "seconds", "direction": "lower",
                "warmups": {key: args.warmups for key in variants}, "runs": []}
    first, warmups, rss = {}, [], {key: [] for key in variants}
    for variant in variants:
        seconds, result, memory = invoke(variant)
        first[variant] = {"value": seconds, "output_hash": digest(encoded(canonical(name, result)))}
    for _ in range(args.warmups):
        for variant in variants:
            seconds, result, memory = invoke(variant)
            warmups.append({"variant": variant, "value": seconds,
                            "output_hash": digest(encoded(canonical(name, result)))})
    for attempt in range(args.runs):
        for variant in variants:
            seconds, result, memory = invoke(variant)
            sequence = len(manifest["runs"]) + 1
            output = args.output / "outputs" / name / f"{sequence:02}-{variant}.json"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(encoded(result) + b"\n")
            if memory:
                rss[variant].append(memory)
            context = "same-host-serial-persistent-mcp-loopback" if name.startswith("mcp_") and name != "mcp_ready" else "same-host-serial-built-artifacts-loopback-fixture"
            manifest["runs"].append({"sequence": sequence, "variant": variant, "value": seconds,
                                      "output_hash": digest(encoded(canonical(name, result))),
                                      "attempt_id": f"{name}-{attempt + 1}-{variant}",
                                      "host": "local-macos-arm64", "context": context,
                                      "raw_output": str(output.relative_to(args.output))})
    evaluation = evaluate(manifest, minimum_runs=5)
    scenario = {"first_uncontrolled_observations": first, "warmups": warmups,
                "manifest": manifest, "evaluation": evaluation}
    if any(rss.values()):
        scenario["mcp_ready_memory"] = {
            variant: {"median_rss_sum_bytes": statistics.median(item["rss_sum_bytes"] for item in samples),
                      "samples": samples} for variant, samples in rss.items()}
    (args.output / f"{name}.manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"scenario": name, **evaluation}), flush=True)
    return scenario


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-binary", type=Path, required=True)
    parser.add_argument("--candidate-binary", type=Path, required=True)
    parser.add_argument("--baseline-name", default="Baseline prototype")
    parser.add_argument("--candidate-name", default="Candidate prototype")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runs", type=int, default=7)
    parser.add_argument("--warmups", type=int, default=2)
    parser.add_argument("--scenario", action="append", choices=SCENARIOS,
                        help="Measure only the selected scenarios; repeatable")
    parser.add_argument("--evaluator", type=Path,
                        help="Optional module exporting evaluate(manifest, minimum_runs) for evidence gating")
    args = parser.parse_args()
    if args.runs < 5 or args.warmups < 1:
        parser.error("require at least five measured runs and one warmup per variant")
    args.output.mkdir(parents=True, exist_ok=False)
    variants = {"baseline": args.baseline_binary.resolve(), "candidate": args.candidate_binary.resolve()}
    evaluate = descriptive_evaluation
    if args.evaluator:
        evaluator_spec = importlib.util.spec_from_file_location("benchmark_evaluator", args.evaluator)
        evaluator = importlib.util.module_from_spec(evaluator_spec)
        evaluator_spec.loader.exec_module(evaluator)
        evaluate = evaluator.evaluate
    report = {"variants": {name: artifact(binary, args.output / "distributions" / f"{name}.tar.gz") for name, binary in variants.items()},
              "environment": {"os": platform.platform(), "architecture": platform.machine(),
                              "cpu_count": os.cpu_count(), "python": platform.python_version(),
                              "load_average_start": os.getloadavg(),
                              "toolchains": {name: subprocess.check_output(command, text=True).strip()
                                             for name, command in (("go", ["go", "version"]),
                                                                   ("rustc", ["rustc", "--version"]),
                                                                   ("cargo", ["cargo", "--version"]))
                                             if shutil.which(command[0])}},
              "method": {"runs_per_variant": args.runs, "warmups_per_variant": args.warmups,
                         "host_label": "local-macos-arm64", "baseline": args.baseline_name,
                         "candidate": args.candidate_name,
                         "memory": "MCP-ready process-tree RSS snapshot sum, not peak; shared pages can be counted twice",
                         "canonicalization": "Remove duration_ms; normalize integral numeric answer formatting; usage unchanged. MCP projection includes negotiated version and full tool names/input/output schemas; raw metadata retained.",
                         "cold_cache": "Not controlled; first observations are already acceptance-exercised artifacts and are not cold-cache claims"},
              "scenarios": {}}
    names = args.scenario or SCENARIOS
    with tempfile.TemporaryDirectory(prefix="decision-comparison-") as folder, provider_server() as (url, requests):
        root = Path(folder)
        for variant, binary in variants.items():
            directory = root / variant
            source = package(directory / "source")
            home = directory / binary.parent.name / "plugins"
            run_cli(binary, ["providers", "install", str(source), "--plugin-home", str(home)])
        for name in names:
            with ExitStack() as sessions:
                if name.startswith("mcp_") and name != "mcp_ready":
                    clients = {}
                    provider = "fixture" if name == "mcp_external_plugin" else "typesafe"
                    payload = image_request() if name == "mcp_typesafe_images" else REQUEST
                    for variant, binary in variants.items():
                        home = root / variant / binary.parent.name / "plugins"
                        clients[variant] = sessions.enter_context(MCP([str(binary), "mcp", "--provider", provider,
                                                                       "--base-url", url, "--plugin-home", str(home)]))
                    def invoke(variant):
                        started = time.perf_counter_ns()
                        response = clients[variant].call("evaluate", payload)
                        seconds = (time.perf_counter_ns() - started) / 1e9
                        if response.get("isError"):
                            raise RuntimeError("persistent MCP evaluation failed")
                        return seconds, structured(response), None
                else:
                    def invoke(variant):
                        return measure(variants[variant], name, root / variant, url)
                report["scenarios"][name] = collect(name, variants, args, evaluate, invoke)
        report["fixture_request_count"] = len(requests)
    report["environment"]["load_average_end"] = os.getloadavg()
    for variant, binary in variants.items():
        if digest(binary.read_bytes()) != report["variants"][variant]["sha256"]:
            raise RuntimeError("an artifact changed during measurement; discard this batch")
    (args.output / "results.json").write_text(json.dumps(report, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
