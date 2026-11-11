"""Image descriptors, default human output, and explicit JSON output."""

import base64
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zlib

from decision_cli import cli
from decision_cli.images import load_images
from decision_cli.output import render_result


def png_bytes():
    """A synthetic valid 280x280 RGB PNG; no user image or model inference."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    pixels = (b"\x00" + b"\xff\x00\x00" * 280) * 280
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 280, 280, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b""))


class ImageAndOutputTests(unittest.TestCase):
    def invoke(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_default_output_and_json_flag_positions(self):
        result = {"model": "fixture", "answers": {"answer": {"type": "noul", "noul": 0.9}}, "usage": {"input_tokens": 5}}
        with patch.object(cli, "evaluate_request", return_value=result):
            code, out, _ = self.invoke("noul", "Is it red?", "--state", "Red")
            self.assertEqual(code, 0)
            self.assertIn("Yes probability: 90.0%", out)
            self.assertIn("Usage: 5 input tokens", out)
            for argv in (("--json", "noul", "Question?", "--state", "x"),
                         ("noul", "Question?", "--state", "x", "--json")):
                code, out, _ = self.invoke(*argv)
                self.assertEqual(code, 0)
                self.assertEqual(json.loads(out), result)
        code, out, _ = self.invoke("noul", "Question?", "--state", "x", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("Request preview (not sent)", out)

    def test_human_output_includes_all_answer_types(self):
        rendered = render_result({"model": "fixture", "usage": {}, "answers": {
            "team": {"type": "choice", "choice": "billing", "confidence": 0.8, "probabilities": {"billing": 0.9, "tech": 0.1}},
            "severity": {"type": "score", "score": 1.6, "confidence": 0.7, "legend": {"0": "Low", "1": "High"}, "probabilities": {"0": 0.2, "1": 0.8}},
            "target": {"type": "box", "box": {"left": 0.1, "top": 0.2, "right": 0.8, "bottom": 0.9}, "confidence": None},
        }})
        for expected in ("Choice: billing", "billing: 90.0%", "Score: 1.6", "High: 80.0%", "Box: left=0.1, top=0.2", "Confidence: 80.0%"):
            self.assertIn(expected, rendered)

    def test_cli_images_attach_without_text_and_resolve_request_relative_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            photo = Path(directory) / "photo.png"
            photo.write_bytes(png_bytes())
            code, out, err = self.invoke("box", "Find the red square", "--image", f"photo={photo}", "--model", "jev-1.14.0", "--dry-run", "--json")
            self.assertEqual(code, 0, err)
            payload = json.loads(out)
            self.assertEqual(payload["state"]["images"]["photo"], "<decision-image:photo>")
            self.assertEqual(load_images(payload, allow_paths=True)["<decision-image:photo>"], png_bytes())
            payload = {"state": {"photo": "<photo>"}, "assets": {"<photo>": {"path": "photo.png"}},
                       "questions": {"red": {"type": "noul", "instructions": "Is the photo red?"}}}
            request = Path(directory) / "request.json"
            request.write_text(json.dumps(payload))
            code, out, err = self.invoke("run", str(request), "--dry-run", "--json")
            self.assertEqual(code, 0, err)
            self.assertEqual(json.loads(out)["assets"]["<photo>"]["path"], str(photo.resolve()))
            code, out, err = self.invoke("noul", "Question?", "--image", str(photo), "--image", str(photo), "--dry-run")
            self.assertEqual(code, 0, err)
            self.assertIn("<decision-image:image_2>", out)

    def test_base64_validation_and_mcp_rejects_paths(self):
        payload = {"state": "<photo>", "questions": {"red": {"type": "noul", "instructions": "Is it red?"}},
                   "assets": {"<photo>": {"data_base64": base64.b64encode(png_bytes()).decode(), "format": "png"}}}
        self.assertEqual(load_images(payload)["<photo>"], png_bytes())
        for asset in ({"data_base64": "not base64"}, {"data_base64": base64.b64encode(b"text").decode()},
                      {"data_base64": base64.b64encode(png_bytes()).decode(), "format": "jpeg"},
                      {"path": "/not-an-image"}):
            with self.assertRaises(ValueError):
                load_images({**payload, "assets": {"<photo>": asset}})
        with self.assertRaisesRegex(ValueError, "referenced"):
            load_images({**payload, "state": "no image reference"})
        with self.assertRaisesRegex(ValueError, "16 MiB"):
            with patch("decision_cli.images.MAX_IMAGE_BYTES", 1):
                load_images(payload)

    def test_missing_and_duplicate_cli_images_fail_before_provider_call(self):
        with patch.object(cli, "evaluate_request", side_effect=AssertionError("must not call provider")):
            for args in (("--image", "/missing/file.png"),
                         ("--image", "photo=one.png", "--image", "photo=two.png")):
                code, out, _ = self.invoke("noul", "Question?", *args, "--dry-run")
                self.assertEqual((code, out), (2, ""))


if __name__ == "__main__":
    unittest.main()
