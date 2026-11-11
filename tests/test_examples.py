"""Execute the generated user-facing examples without provider calls."""

from contextlib import chdir, redirect_stdout
import io
import json
from pathlib import Path
import re
import shlex
import shutil
import tempfile
import unittest
from unittest.mock import patch

from decision_cli import cli
from test_images import png_bytes

REFERENCES = Path(__file__).resolve().parents[1] / "skills/decision/references"


class DocumentedExamplesTests(unittest.TestCase):
    def test_all_documented_query_commands_validate_without_inference(self):
        guide = (REFERENCES / "examples.md").read_text()
        commands = []
        for block in re.findall(r"```bash\n(.*?)```", guide, re.S):
            commands.extend(shlex.split(line) for line in block.replace("\\\n", "").strip().splitlines() if line.strip())
        self.assertTrue(commands, "No executable examples found")
        seen_commands, seen_types = set(), set()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("dog.jpg", "before.jpg", "after.jpg"):
                (root / name).write_bytes(png_bytes())
            for name in ("dog-request.json", "dog-questions.json"):
                shutil.copyfile(REFERENCES / name, root / name)
            with chdir(root), patch.object(cli, "evaluate_request", side_effect=AssertionError("Examples must never call a provider")):
                for command in commands:
                    with self.subTest(command=command):
                        self.assertEqual(command[0], "decision")
                        args = command[1:]
                        seen_commands.add(args[0])
                        for flag in ("--dry-run", "--json"):
                            if flag not in args:
                                args.append(flag)
                        output = io.StringIO()
                        with redirect_stdout(output):
                            self.assertEqual(cli.main(args), 0)
                        request = json.loads(output.getvalue())
                        self.assertTrue(request["questions"])
                        seen_types.update(q["type"] for q in request["questions"].values())
        self.assertTrue({"choice", "noul", "score", "box", "ask", "run"} <= seen_commands)
        self.assertEqual(seen_types, {"choice", "noul", "score", "box"})


if __name__ == "__main__":
    unittest.main()
