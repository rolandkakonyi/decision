"""Native image validation and human/JSON terminal output."""

import base64
import json
from pathlib import Path
import tempfile
import unittest

from native import evaluate, invoke
from provider_fixtures import png_bytes


class ImageAndOutputTests(unittest.TestCase):
    def test_default_output_and_json_flag_positions(self):
        payload = {"state": "red", "questions": {"answer": {"type": "noul", "instructions": "Is it red?"}}}
        response = {"model": "fixture", "answers": {"answer": {"type": "noul", "noul": 0.9}}, "usage": {"input_tokens": 5, "output_tokens": None}}
        from native import response_server
        with response_server(response) as (url, _):
            settings = {"DECISION_API_KEY": "fixture"}
            result = invoke("noul", "Is it red?", "--state", "red", "--base-url", url, env=settings)
            self.assertEqual(result.returncode, 0, result.stderr)
            for value in ("Yes probability: 90.0%", "Usage: 5 input tokens", "output tokens unavailable", "Duration:"):
                self.assertIn(value, result.stdout)
            for argv in (("--json", "noul", "Question?", "--state", "x"), ("noul", "Question?", "--state", "x", "--json")):
                result = invoke(*argv, "--base-url", url, env=settings)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)["answers"], response["answers"])
        result = invoke("run", "-", "--dry-run", data=json.dumps(payload))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Request preview (not sent)", result.stdout)

    def test_human_output_all_types_zero_usage_and_control_characters(self):
        payload = {"state": "x", "questions": {"team": {"type": "choice", "instructions": "Pick", "criteria": {"billing": "Billing", "tech": "Tech"}},
                   "severity": {"type": "score", "instructions": "Rate", "criteria": ["Low", "High"]},
                   "target": {"type": "box", "instructions": "Locate"}}}
        response = {"model": "fixture\x1b[31m", "usage": {"input_tokens": 0, "output_tokens": 0}, "answers": {
            "team": {"type": "choice", "choice": "billing", "confidence": 0.8, "probabilities": {"billing": 0.9, "tech": 0.1}},
            "severity": {"type": "score", "score": 0.8, "confidence": 0.7, "legend": {"0": "Low", "1": "High"}, "probabilities": {"0": 0.2, "1": 0.8}},
            "target": {"type": "box", "box": {"left": 0.1, "top": 0.2, "right": 0.8, "bottom": 0.9}, "confidence": None}}}
        from native import response_server
        with response_server(response) as (url, _):
            result = invoke("run", "-", "--base-url", url, data=json.dumps(payload), env={"DECISION_API_KEY": "fixture"})
        self.assertEqual(result.returncode, 0, result.stderr)
        for expected in ("Choice: billing", "billing: 90.0%", "Score: 0.8", "High: 80.0%", "Box: left=0.1, top=0.2", "Confidence: 80.0%", "Usage: 0 input tokens, 0 output tokens"):
            self.assertIn(expected, result.stdout)
        self.assertNotIn("\x1b", result.stdout)

    def test_cli_images_without_text_and_request_relative_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            photo = root / "photo.png"
            photo.write_bytes(png_bytes())
            result = invoke("box", "Find the red square", "--image", f"photo={photo}", "--dry-run", "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["state"]["images"]["photo"], "<decision-image:photo>")
            payload = {"state": "<photo>", "questions": {"red": {"type": "noul", "instructions": "Red?"}}, "assets": {"<photo>": {"path": "photo.png"}}}
            request = root / "request.json"
            request.write_text(json.dumps(payload))
            result = invoke("run", request, "--dry-run", "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
            preview_path = Path(json.loads(result.stdout)["assets"]["<photo>"]["path"])
            self.assertTrue(preview_path.is_absolute())
            self.assertEqual(preview_path.resolve(), photo.resolve())
            result = invoke("noul", "Question?", "--image", photo, "--image", photo, "--dry-run")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("<decision-image:image_2>", result.stdout)

    def test_base64_signature_format_reference_and_size_checks(self):
        payload = {"state": "<photo>", "questions": {"red": {"type": "noul", "instructions": "Red?"}}}
        good = {"data_base64": base64.b64encode(png_bytes()).decode(), "format": "png"}
        result = invoke("run", "-", "--dry-run", "--json", data=json.dumps({**payload, "assets": {"<photo>": good}}))
        self.assertEqual(result.returncode, 0, result.stderr)
        for asset in ({"data_base64": "not base64"}, {"data_base64": base64.b64encode(b"text").decode()},
                      {**good, "format": "jpeg"}, {"path": "/missing-image"},
                      {"data_base64": base64.b64encode(png_bytes() + b"x" * (16 * 1024 * 1024)).decode()}):
            result = invoke("run", "-", "--dry-run", "--json", data=json.dumps({**payload, "assets": {"<photo>": asset}}))
            self.assertEqual((result.returncode, result.stdout), (2, ""))
        result = invoke("run", "-", "--dry-run", "--json", data=json.dumps({**payload, "state": "unreferenced", "assets": {"<photo>": good}}))
        self.assertEqual((result.returncode, result.stdout), (2, ""))

    def test_missing_and_duplicate_flag_images_fail_before_provider(self):
        for flags in (("--image", "/missing/file.png"), ("--image", "photo=one.png", "--image", "photo=two.png")):
            result = invoke("noul", "Question?", *flags, "--dry-run")
            self.assertEqual((result.returncode, result.stdout), (2, ""))


if __name__ == "__main__":
    unittest.main()
