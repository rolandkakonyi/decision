"""Execute the native generator's user-facing examples without provider calls."""

import base64
import json
from pathlib import Path
import re
import shlex
import shutil
import tempfile
import unittest

from native import invoke, response_server

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a6y8AAAAASUVORK5CYII=")


class DocumentedExamplesTests(unittest.TestCase):
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
