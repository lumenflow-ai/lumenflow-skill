from __future__ import annotations

import copy
import unittest
from unittest.mock import patch
import io

from test_client import FakeOpener, FakeResponse, LumenflowClient, LumenflowError, config
import lumenflow as cli
from _contract import validate_public_response
from _review import validate_review_response


def material():
    return {
        "project_id": "prj_customer", "episode_from": 31, "episode_to": 40,
        "observed_at": 1, "consistency": "per_page", "missing_episode_numbers": [32],
        "episode": {
            "id": "ep_31", "episode_number": 31, "title": None, "parse_status": "completed",
            "source": {"status": "available", "provenance": "stored_episode_offsets", "script_content": "甲开门。", "version": "a" * 64},
            "scenes": [{"id": "scn_1", "scene_number": 1, "status": "draft", "duration": "4s",
                        "prompt": "画面：甲开门。", "content_status": "available", "edit_version": "b" * 64,
                        "asset_references": [{"id": "ast_1", "name": "甲", "category": "character"}]}],
        },
        "next_cursor": "opaque.cursor",
    }


class ReviewTests(unittest.TestCase):
    def test_cli_get_only_and_bounded_pagination(self):
        opener = FakeOpener([FakeResponse(material())])
        client = LumenflowClient(config(), opener=opener)
        args = cli.build_parser().parse_args(["review-materials", "prj_customer", "--episode-from", "31", "--episode-to", "40", "--limit", "1", "--after", "opaque.cursor"])
        result = cli.execute(args, client)
        self.assertEqual(result["next_cursor"], "opaque.cursor")
        self.assertEqual(len(opener.requests), 1)
        self.assertEqual(opener.requests[0].method, "GET")
        self.assertIn("/review-materials?episode_from=31&episode_to=40&limit=1&after=opaque.cursor", opener.requests[0].full_url)
        self.assertIsNone(opener.requests[0].data)

    def test_normal_response_policy_still_blocks_business_text(self):
        value = material()
        validate_review_response(value)
        with self.assertRaises(LumenflowError):
            validate_public_response(value)
        opener = FakeOpener([FakeResponse(value)])
        with self.assertRaises(LumenflowError):
            LumenflowClient(config(), opener=opener).records("prj_customer", "scene-records")

    def test_unknown_fields_and_nested_text_are_blocked_at_every_depth(self):
        for location in ("root", "episode", "source", "scene", "asset"):
            value = material()
            target = {"root": value, "episode": value["episode"], "source": value["episode"]["source"],
                      "scene": value["episode"]["scenes"][0], "asset": value["episode"]["scenes"][0]["asset_references"][0]}[location]
            target["system_prompt"] = "private"
            with self.assertRaises(LumenflowError):
                validate_review_response(value)
        value = material()
        value["episode"]["source"]["script_content"] = {"system_config": "private"}
        with self.assertRaises(LumenflowError):
            validate_review_response(value)

    def test_missing_source_is_explicit_and_wrong_project_is_rejected(self):
        value = material()
        value["episode"]["source"] = {"status": "missing", "provenance": None, "script_content": None, "version": None}
        validate_review_response(value)
        value["project_id"] = "prj_other"
        opener = FakeOpener([FakeResponse(value)])
        with self.assertRaises(LumenflowError):
            LumenflowClient(config(), opener=opener).review_materials("prj_customer", episode_from=31, episode_to=40)

    def test_invalid_input_fails_before_network_and_range_is_required(self):
        opener = FakeOpener([])
        client = LumenflowClient(config(), opener=opener)
        for start, end, limit in ((0, 40, 1), (31, 51, 1), (40, 31, 1), (31, 40, 51), (True, 2, 1)):
            with self.assertRaises(LumenflowError):
                client.review_materials("prj_customer", episode_from=start, episode_to=end, limit=limit)
        self.assertEqual(opener.requests, [])
        with patch("sys.stderr", new=io.StringIO()), self.assertRaises(SystemExit):
            cli.build_parser().parse_args(["review-materials", "prj_customer"])

    def test_partial_or_oversized_payloads_are_not_silently_accepted(self):
        value = material()
        for mutate in (
            lambda x: x.pop("missing_episode_numbers"),
            lambda x: x["episode"]["source"].update(script_content="剧" * 100000),
            lambda x: x["episode"]["source"].update(status="missing"),
            lambda x: x["episode"].update(episode_number=99),
            lambda x: x["episode"]["scenes"][0].update(prompt="镜" * 12000),
        ):
            candidate = copy.deepcopy(value)
            mutate(candidate)
            with self.assertRaises(LumenflowError):
                validate_review_response(candidate)
