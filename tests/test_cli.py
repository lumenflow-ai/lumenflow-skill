from __future__ import annotations

import argparse
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

SCRIPTS_DIR = Path(__file__).parents[1] / "lumenflow" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from _client import ClientConfig, LumenflowError

import lumenflow as cli


class FakeClient:
    def __init__(
        self,
        progress: list[dict] | None = None,
        *,
        assets: list[dict] | None = None,
        seedance_jobs: list[dict] | None = None,
    ) -> None:
        self.progress_results = list(progress or [])
        self.asset_results = list(assets or [])
        self.seedance_job_results = list(seedance_jobs or [])
        self.calls: list[tuple] = []
        self.last_retry_after: float | None = None
        self.last_request_id: str | None = "req_test"

    def session(self) -> dict:
        self.calls.append(("session",))
        return {"authenticated": True, "scopes": ["project:read"]}

    def styles(self) -> dict:
        self.calls.append(("styles",))
        return {"styles": []}

    def create_project(self, **kwargs: object) -> dict:
        self.calls.append(("create", kwargs))
        return {
            "project_id": "prj_123",
            "status": "created",
            "project_url": "https://lumenflow.example/projects/prj_123",
        }

    def parse_project(
        self,
        project_id: str,
        *,
        idempotency_key: str | None = None,
    ) -> dict:
        self.calls.append(("parse", project_id, idempotency_key))
        return {"project_id": project_id, "status": "parsing"}

    def progress(self, project_id: str) -> dict:
        self.calls.append(("progress", project_id))
        return self.progress_results.pop(0)

    def generate(self, project_id: str, **kwargs: object) -> dict:
        self.calls.append(("generate", project_id, kwargs))
        return {"project_id": project_id, "status": "generating"}

    def assets(self, project_id: str) -> dict:
        self.calls.append(("assets", project_id))
        return {"project_id": project_id, "assets": self.asset_results}

    def seedance25_capabilities(self) -> dict:
        self.calls.append(("seedance25_capabilities",))
        return {"workbench": "seedance-2.5"}

    def seedance25_upload(
        self,
        file_path: Path,
        *,
        idempotency_key: str | None = None,
    ) -> dict:
        self.calls.append(("seedance25_upload", file_path, idempotency_key))
        return {"asset_id": f"swa_{len(self.calls)}", "media_kind": "image"}

    def seedance25_create_job(self, **kwargs: object) -> dict:
        self.calls.append(("seedance25_create_job", kwargs))
        return {"job_id": "swj_123", "status": "queued"}

    def seedance25_job(self, job_id: str) -> dict:
        self.calls.append(("seedance25_job", job_id))
        return self.seedance_job_results.pop(0)

    def seedance25_assets(self, job_id: str) -> dict:
        self.calls.append(("seedance25_assets", job_id))
        return {"job_id": job_id, "assets": self.asset_results}

    def download(
        self,
        url: str,
        destination: Path,
        *,
        overwrite: bool,
        max_bytes: int | None,
    ) -> Path:
        self.calls.append(("download", url, destination.name, overwrite, max_bytes))
        return destination.resolve()


def workflow_args(*extra: str) -> argparse.Namespace:
    return cli.build_parser().parse_args(
        [
            "workflow", "--idempotency-key", "test-workflow-key",
            "--title",
            "Episode 1",
            "--script",
            "A complete script for testing.",
            "--poll-interval",
            "1",
            "--max-wait",
            "10",
            *extra,
        ]
    )


def seedance25_workflow_args(*extra: str) -> argparse.Namespace:
    return cli.build_parser().parse_args(
        [
            "seedance25-workflow", "--idempotency-key", "test-seedance-key",
            "--prompt",
            "A cinematic user-authored prompt.",
            "--duration",
            "8",
            "--aspect-ratio",
            "16:9",
            "--poll-interval",
            "1",
            "--max-wait",
            "10",
            *extra,
        ]
    )


class CliInputTests(unittest.TestCase):
    def test_script_file_must_be_supported_utf8_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            unsupported = Path(temp_dir) / "script.pdf"
            unsupported.write_bytes(b"not a pdf")
            args = cli.build_parser().parse_args(
                [
                    "create", "--idempotency-key", "test-create-key",
                    "--title",
                    "Episode",
                    "--file",
                    str(unsupported),
                ]
            )
            with self.assertRaises(LumenflowError) as caught:
                cli.execute(args, FakeClient())
            self.assertEqual(caught.exception.code, "invalid_input")

            invalid_utf8 = Path(temp_dir) / "script.md"
            invalid_utf8.write_bytes(b"\xff\xfe")
            args.file = invalid_utf8
            with self.assertRaises(LumenflowError) as caught:
                cli.execute(args, FakeClient())
            self.assertIn("UTF-8", caught.exception.message)

    def test_short_script_and_long_title_are_rejected(self) -> None:
        short = cli.build_parser().parse_args(
            ["create", "--idempotency-key", "test-create-key", "--title", "Episode", "--script", "short"]
        )
        with self.assertRaises(LumenflowError):
            cli.execute(short, FakeClient())

        long_title = cli.build_parser().parse_args(
            [
                "create", "--idempotency-key", "test-create-key",
                "--title",
                "x" * 121,
                "--script",
                "A complete script.",
            ]
        )
        with self.assertRaises(LumenflowError):
            cli.execute(long_title, FakeClient())

    def test_download_requires_generation_in_workflow(self) -> None:
        args = workflow_args("--download-dir", "/tmp/results")
        with self.assertRaises(LumenflowError) as caught:
            cli.run_workflow(FakeClient(), args)
        self.assertEqual(caught.exception.code, "invalid_input")


class WorkflowTests(unittest.TestCase):
    def test_workflow_stops_after_parse_without_explicit_generation(self) -> None:
        client = FakeClient(progress=[{"status": "ready"}])
        args = workflow_args()

        result = cli.run_workflow(
            client,
            args,
            sleep=lambda _: None,
            monotonic=lambda: 0.0,
            random_fn=lambda: 0.5,
        )

        self.assertFalse(result["generation_submitted"])
        self.assertNotIn("generate", [call[0] for call in client.calls])

    def test_explicit_generation_waits_for_completion(self) -> None:
        client = FakeClient(progress=[{"status": "ready"}, {"status": "completed"}])
        args = workflow_args("--generate")

        result = cli.run_workflow(
            client,
            args,
            sleep=lambda _: None,
            monotonic=lambda: 0.0,
            random_fn=lambda: 0.5,
        )

        self.assertTrue(result["generation_submitted"])
        self.assertFalse(result["wait_timed_out"])
        self.assertIn("generate", [call[0] for call in client.calls])

    def test_workflow_derives_stable_idempotency_keys(self) -> None:
        client = FakeClient(progress=[{"status": "ready"}, {"status": "completed"}])
        args = workflow_args(
            "--generate",
            "--idempotency-key",
            "workflow-123456",
        )

        cli.run_workflow(
            client,
            args,
            sleep=lambda _: None,
            monotonic=lambda: 0.0,
            random_fn=lambda: 0.5,
        )

        create = next(call for call in client.calls if call[0] == "create")
        parse = next(call for call in client.calls if call[0] == "parse")
        generate = next(call for call in client.calls if call[0] == "generate")
        self.assertEqual(
            create[1]["idempotency_key"],
            "workflow-123456:create",
        )
        self.assertEqual(parse[2], "workflow-123456:parse")
        self.assertEqual(
            generate[2]["idempotency_key"],
            "workflow-123456:generate",
        )

    def test_timeout_returns_resumable_project_instead_of_looping_forever(
        self,
    ) -> None:
        client = FakeClient(progress=[{"status": "parsing"}])
        args = workflow_args("--max-wait", "1")
        times = iter([0.0, 1.0])

        result = cli.run_workflow(
            client,
            args,
            sleep=lambda _: None,
            monotonic=lambda: next(times),
            random_fn=lambda: 0.5,
        )

        self.assertTrue(result["wait_timed_out"])
        self.assertEqual(result["project_id"], "prj_123")

    def test_failed_parse_returns_public_failure(self) -> None:
        client = FakeClient(progress=[{"status": "parse_failed"}])
        args = workflow_args()

        with self.assertRaises(LumenflowError) as caught:
            cli.run_workflow(
                client,
                args,
                sleep=lambda _: None,
                monotonic=lambda: 0.0,
            )

        self.assertEqual(caught.exception.code, "parse_failed")
        self.assertEqual(caught.exception.request_id, "req_test")


class Seedance25WorkflowTests(unittest.TestCase):
    def test_generation_requires_explicit_confirmation(self) -> None:
        client = FakeClient()
        args = seedance25_workflow_args()

        with self.assertRaises(LumenflowError) as caught:
            cli.run_seedance25_workflow(client, args)

        self.assertEqual(caught.exception.code, "generation_confirmation_required")
        self.assertNotIn("seedance25_create_job", [call[0] for call in client.calls])

    def test_workflow_uses_separate_assets_jobs_and_stable_keys(self) -> None:
        client = FakeClient(seedance_jobs=[{"job_id": "swj_123", "status": "completed"}])
        args = seedance25_workflow_args(
            "--confirm-generation",
            "--reference",
            "/tmp/character.png",
            "--idempotency-key",
            "seedance-workflow-123",
        )

        result = cli.run_seedance25_workflow(
            client,
            args,
            sleep=lambda _: None,
            monotonic=lambda: 0.0,
            random_fn=lambda: 0.5,
        )

        upload = next(call for call in client.calls if call[0] == "seedance25_upload")
        create = next(call for call in client.calls if call[0] == "seedance25_create_job")
        self.assertEqual(upload[2], "seedance-workflow-123:upload:1")
        self.assertEqual(create[1]["idempotency_key"], "seedance-workflow-123:create")
        self.assertEqual(create[1]["asset_ids"], [result["uploads"][0]["asset_id"]])
        self.assertEqual(result["job_id"], "swj_123")
        self.assertFalse(result["wait_timed_out"])

    def test_unknown_job_state_is_blocked(self) -> None:
        client = FakeClient(seedance_jobs=[{"job_id": "swj_123", "status": "provider_running"}])
        args = seedance25_workflow_args("--confirm-generation")

        with self.assertRaises(LumenflowError) as caught:
            cli.run_seedance25_workflow(
                client,
                args,
                sleep=lambda _: None,
                monotonic=lambda: 0.0,
            )

        self.assertEqual(caught.exception.code, "invalid_response")


class DownloadWorkflowTests(unittest.TestCase):
    def test_download_sanitizes_and_deduplicates_filenames(self) -> None:
        client = FakeClient(
            assets=[
                {
                    "asset_id": "ast_1",
                    "filename": "../../scene one.mp4",
                    "download_url": "https://cdn.example.com/a?sig=secret",
                },
                {
                    "asset_id": "ast_2",
                    "filename": "scene one.mp4",
                    "download_url": "https://cdn.example.com/b?sig=secret",
                },
            ]
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            result = cli._download_assets(
                client,
                "prj_123",
                Path(temp_dir),
                asset_ids=None,
                overwrite=False,
                max_bytes=None,
            )

        names = [item["filename"] for item in result["files"]]
        self.assertEqual(names, ["scene_one.mp4", "scene_one-2.mp4"])

    def test_unknown_selected_asset_does_not_leak_asset_list(self) -> None:
        client = FakeClient(assets=[])
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            self.assertRaises(LumenflowError) as caught,
        ):
            cli._download_assets(
                client,
                "prj_123",
                Path(temp_dir),
                asset_ids=["ast_other"],
                overwrite=False,
                max_bytes=None,
            )
        self.assertEqual(caught.exception.code, "asset_not_found")


class MainTests(unittest.TestCase):
    def test_missing_credentials_return_machine_readable_error(self) -> None:
        stderr = io.StringIO()
        with patch.dict(os.environ, {}, clear=True), redirect_stderr(stderr):
            code = cli.main(["auth-check"])

        payload = json.loads(stderr.getvalue())
        self.assertEqual(code, 3)
        self.assertEqual(
            payload["error"]["code"],
            "authentication_required",
        )

    def test_success_output_never_contains_token(self) -> None:
        stdout = io.StringIO()
        client = FakeClient()

        def factory(config: ClientConfig) -> FakeClient:
            self.assertNotIn("lf_private", repr(config))
            return client

        env = {
            "LUMENFLOW_API_BASE_URL": "https://api.lumenflow.net/open/v1",
            "LUMENFLOW_API_KEY": "lf_private",
        }
        with (
            patch.dict(os.environ, env, clear=True),
            redirect_stdout(stdout),
        ):
            code = cli.main(["auth-check"], client_factory=factory)

        self.assertEqual(code, 0)
        self.assertNotIn("lf_private", stdout.getvalue())
        self.assertTrue(json.loads(stdout.getvalue())["ok"])


class OfflineConversationBoundaryTests(unittest.TestCase):
    def test_help_and_version_never_construct_api_client(self):
        for argv in (["--version"], ["--help"], ["auth-check", "--help"],
                     ["canvas-models", "--help"], ["canvas-quote", "--help"],
                     ["canvas-download", "--help"]):
            with self.subTest(argv=argv), patch.dict(os.environ, {}, clear=True), \
                    redirect_stdout(io.StringIO()), \
                    patch.object(cli, "ClientConfig") as config:
                with self.assertRaises(SystemExit) as result:
                    cli.main(argv)
                self.assertEqual(result.exception.code, 0)
                config.from_env.assert_not_called()

    def test_missing_token_stops_before_transport_creation(self):
        for argv in (["auth-check"], ["canvas-models", "canvas_example"]):
            with self.subTest(argv=argv), patch.dict(os.environ, {}, clear=True), \
                    redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as err:
                factory = Mock(side_effect=AssertionError("No API transport"))
                self.assertNotEqual(cli.main(argv, client_factory=factory), 0)
                factory.assert_not_called()
                self.assertIn("authentication_required", err.getvalue())


if __name__ == "__main__":
    unittest.main()
