from __future__ import annotations

import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from test_client import FakeOpener, FakeResponse, LumenflowClient, LumenflowError, config, request_headers
import lumenflow as cli


class BatchTests(unittest.TestCase):
    def test_batch_prompt_patch_preserves_key_and_uses_public_route(self):
        opener = FakeOpener([FakeResponse({"items": [{"scene_id": "scn_1", "edit_version": "v"}]})])
        client = LumenflowClient(config(), opener=opener)
        payload = {"items": [{"scene_id": "scn_1", "expected_edit_version": "v", "prompt": "business text"}]}
        client.batch("prj_1", "prompts", payload, idempotency_key="test-batch-key")
        request = opener.requests[0]
        self.assertEqual(request.method, "PATCH")
        self.assertEqual(request.full_url, "https://api.example.com/open/v1/projects/prj_1/scenes/prompts/batch")
        self.assertEqual(request_headers(request)["idempotency-key"], "test-batch-key")
        self.assertEqual(json.loads(request.data), payload)

    def test_bounds_and_identity_injection_rejected_before_network(self):
        opener = FakeOpener([])
        client = LumenflowClient(config(), opener=opener)
        for payload in ({"items": []}, {"items": [{}] * 21}, {"items": [{"owner_id": "usr_other"}]}):
            with self.assertRaises(LumenflowError):
                client.batch("prj_1", "episodes", payload, idempotency_key="test-batch-key")
        self.assertEqual(opener.requests, [])

    def test_failure_pagination_and_manual_creation(self):
        opener = FakeOpener([FakeResponse({"items": [], "next_cursor": None}), FakeResponse({"project_id": "prj_1"})])
        client = LumenflowClient(config(), opener=opener)
        client.records("prj_1", "failures", after="scn_previous", limit=10)
        self.assertIn("/failures?limit=10&after=scn_previous", opener.requests[0].full_url)
        client.create_empty("empty", idempotency_key="test-manual-key")
        self.assertEqual(json.loads(opener.requests[1].data), {"title": "empty", "creation_mode": "manual"})

    def test_cli_requires_explicit_key_for_batch(self):
        with patch("sys.stderr", new=io.StringIO()), self.assertRaises(SystemExit):
            cli.build_parser().parse_args(["batch-assets", "prj_1", "--file", "records.json"])

    def test_cli_file_dispatch_does_not_print_business_prompt(self):
        opener = FakeOpener([FakeResponse({"items": [], "batch_id": "batch_1"})])
        client = LumenflowClient(config(), opener=opener)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "batch.json"
            path.write_text(json.dumps({"items": [{"title": "Episode"}]}), encoding="utf-8")
            args = cli.build_parser().parse_args(["batch-episodes", "prj_1", "--file", str(path), "--idempotency-key", "test-batch-key"])
            result = cli.execute(args, client)
        self.assertEqual(result["batch_id"], "batch_1")
        self.assertNotIn("Episode", json.dumps(result))
