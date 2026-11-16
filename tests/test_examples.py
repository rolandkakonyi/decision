"""Execute the native generator's user-facing examples without provider calls."""

import base64
import hashlib
import json
from pathlib import Path
import re
import runpy
import shlex
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

from native import invoke, response_server

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a6y8AAAAASUVORK5CYII=")


class DocumentedExamplesTests(unittest.TestCase):
    def test_wdc_preparation_keeps_labels_and_deduplicates_products(self):
        module = runpy.run_path(str(Path(__file__).resolve().parents[1] / "examples/hierarchical/prepare_wdc.py"))
        prepare = module["prepare"]
        fields = {name: {} for name in ("CategoryID", "CategoryName", "pathlist_ids", "pathlist_names", "title", "desc", "gtin", "cluster_id")}
        for index in range(5):
            product = min(index, 3)
            row = str(index)
            values = (f"leaf{product}", f"Class {product}", ["root", f"group{product//2}", f"leaf{product}"],
                      ["Catalog", f"Department {product//2}", f"Class {product}"], f"SKU {product}",
                      None if product == 0 else f"Merchant text {product}", str(1000+product), product)
            for name, value in zip(fields, values):
                fields[name][row] = value
        source = json.dumps(fields)
        data, manifest = prepare(source, 4)
        self.assertEqual(prepare(source, 4), (data, manifest))
        self.assertEqual(len(data["cases"]), 4)
        self.assertEqual(len({row["gtin"] for row in manifest["selected"]}), 4)
        self.assertEqual(len({row["cluster_id"] for row in manifest["selected"]}), 4)
        self.assertEqual({row["expected"] for row in data["cases"]}, {f"leaf{i}" for i in range(4)})
        for case, selected in zip(data["cases"], manifest["selected"]):
            row = selected["source_row_id"]
            self.assertEqual(case["expected"], fields["CategoryID"][row])
            self.assertEqual(case["text"], "Title: " + fields["title"][row] + "\nDescription: " + (fields["desc"][row] or ""))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path, output_path = root / "source.json", root / "sample.json"
            source_path.write_bytes(source.encode())
            with patch.dict(module["main"].__globals__, {"SOURCE_SHA256": hashlib.sha256(source.encode()).hexdigest()}), \
                    patch.object(sys, "argv", ["prepare_wdc.py", "--source", str(source_path), "--output", str(output_path), "--count", "4"]), \
                    patch("sys.stdout"):
                module["main"]()
            stored = json.loads(output_path.with_suffix(".manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(stored["dataset_sha256"], hashlib.sha256(output_path.read_bytes()).hexdigest())
        fields["pathlist_names"]["0"][-1] = "Conflicting category"
        with self.assertRaises(ValueError):
            prepare(json.dumps(fields), 4)

    def test_all_documented_query_commands_validate_without_inference(self):
        with tempfile.TemporaryDirectory() as directory, response_server({}) as (url, requests):
            root = Path(directory)
            exported = root / "skill"
            result = invoke("skill", "--output", exported)
            self.assertEqual(result.returncode, 0, result.stderr)
            references = exported / "references"
            guide = (references / "examples.md").read_text(encoding="utf-8")
            commands = []
            for block in re.findall(r"```bash\n(.*?)```", guide, re.S):
                commands.extend(shlex.split(line) for line in block.replace("\\\n", "").strip().splitlines() if line.strip())
            self.assertTrue(commands, "No executable examples found")
            seen_commands, seen_types = set(), set()
            for name in ("dog.jpg", "before.jpg", "after.jpg"):
                (root / name).write_bytes(PNG)
            for name in ("dog-request.json", "dog-questions.json", "dog-mixed-request.json", "dog-mixed-questions.json",
                         "openai-dog-request.json", "openai-dog-questions.json", "cloudflare-dog-request.json",
                         "cloudflare-dog-questions.json", "gemini-dog-request.json", "gemini-dog-questions.json"):
                shutil.copyfile(references / name, root / name)
            for command in commands:
                with self.subTest(command=command):
                    self.assertEqual(command[0], "decision")
                    args = command[1:]
                    seen_commands.add(args[0])
                    for flag in ("--dry-run", "--json"):
                        if flag not in args:
                            args.append(flag)
                    result = invoke(*args, cwd=root, env={"DECISION_BASE_URL": url})
                    self.assertEqual(result.returncode, 0, result.stderr)
                    request = json.loads(result.stdout)
                    self.assertTrue(request["questions"])
                    seen_types.update(q["type"] for q in request["questions"].values())
                    self.assertEqual(requests, [], "A documented dry run contacted the provider")
            self.assertTrue({"choice", "noul", "score", "box", "ask", "run"} <= seen_commands)
            self.assertEqual(seen_types, {"choice", "noul", "score", "box"})


if __name__ == "__main__":
    unittest.main()
