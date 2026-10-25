"""Offline checks for the developer-only Seedream client.

These tests replace ``urllib.request.urlopen`` with a small fake response, so
running them never sends a request to Ark or downloads an external image.
"""

from __future__ import annotations

import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import seedream_api


class _Response:
    def __init__(self, body: bytes, content_type: str = "application/json") -> None:
        self.body = body
        self.headers = {"Content-Type": content_type}

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


class SeedreamTests(unittest.TestCase):
    def test_local_image_is_data_url_and_dry_payload_has_image_list(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "ref.png"
            image.write_bytes(b"fake-png")
            payload = seedream_api.build_payload("修改服装", images=[image])
        self.assertEqual(payload["image"][0].split(",", 1)[0], "data:image/png;base64")
        self.assertEqual(base64.b64decode(payload["image"][0].split(",", 1)[1]), b"fake-png")

    def test_client_saves_b64_response_and_metadata_without_network(self) -> None:
        png = base64.b64encode(b"fake-png").decode("ascii")
        response = _Response(json.dumps({"data": [{"b64_json": png}]}).encode())
        with tempfile.TemporaryDirectory() as directory, patch.object(seedream_api.urllib.request, "urlopen", return_value=response):
            result = seedream_api.SeedreamClient("test-key").generate("生成妮可", output_dir=directory, output_prefix="idle")
            output = Path(directory) / "idle-01.png"
            metadata = Path(directory) / "idle.json"
            self.assertEqual(result.image_urls, ())
            self.assertEqual(result.output_files, (output,))
            self.assertEqual(output.read_bytes(), b"fake-png")
            self.assertEqual(json.loads(metadata.read_text())["data"][0]["b64_json"], png)

    def test_client_posts_auth_and_downloads_url_response(self) -> None:
        calls: list[object] = []
        api_response = _Response(json.dumps({"data": [{"url": "https://example.invalid/result.png"}]}).encode())
        image_response = _Response(b"fake-png", content_type="image/png")

        def fake_open(request: object, timeout: float = 0) -> _Response:
            calls.append(request)
            return api_response if len(calls) == 1 else image_response

        with tempfile.TemporaryDirectory() as directory, patch.object(seedream_api.urllib.request, "urlopen", side_effect=fake_open):
            result = seedream_api.SeedreamClient("secret-key").generate("生成妮可", output_dir=directory)
            self.assertEqual(result.output_files[0].read_bytes(), b"fake-png")
        request = calls[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer secret-key")
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["prompt"], "生成妮可")
        self.assertNotIn("secret-key", request.data.decode("utf-8"))

    def test_missing_key_fails_before_network(self) -> None:
        with patch.object(seedream_api.urllib.request, "urlopen") as opener:
            with self.assertRaises(seedream_api.SeedreamConfigError):
                seedream_api.SeedreamClient("").generate("生成妮可")
            opener.assert_not_called()


if __name__ == "__main__":
    unittest.main()
