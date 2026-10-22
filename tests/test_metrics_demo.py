from __future__ import annotations

import asyncio
import json
import socket
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from cli import app
from skills.demo import run_demo
from skills.metrics import RunMetrics, record_request, record_usage


async def test_demo_is_offline_and_writes_complete_package(tmp_path, monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("Demo attempted network access")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setenv("GEMINI_API_KEY", "must-not-be-used")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "must-not-be-used")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "must-not-be-used")
    package = await run_demo(tmp_path)
    from pathlib import Path
    root = Path(package.output_dir)
    assert package.status == "completed"
    assert {p.name for p in root.iterdir()} == {
        "application.json", "metrics.json", "cover_letter.md", "company_brief.md",
        "resume_report.md", "profile.json", "job.txt", "responses.json",
    }
    assert json.loads((root / "application.json").read_text())["jd"]["company"] == package.jd.company
    assert len(package.metrics.stages) == 5
    assert all(not m.requests and m.estimated_cost_usd == 0 for m in package.metrics.stages.values())
    assert "Alex Morgan" in (root / "cover_letter.md").read_text()


def test_demo_cli(tmp_path):
    result = CliRunner().invoke(app, ["demo", "--output-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "No API requests" in result.output


async def test_demo_does_not_read_production_settings_or_create_checkpoints(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Demo accessed production recovery state")
    monkeypatch.setattr("agents.orchestrator.get_settings", forbidden)
    monkeypatch.setattr("agents.orchestrator.CheckpointStore", forbidden)
    package = await run_demo(tmp_path)
    assert package.status == "completed"
    assert package.checkpoint_path is None
    assert not (tmp_path / ".runs").exists()


async def test_accounting_inherits_search_tasks_and_isolates_runs():
    async def run(count):
        metrics = RunMetrics()
        with metrics.stage("research"):
            async def query():
                await asyncio.sleep(0)
                record_request("serper")
            await asyncio.gather(*(query() for _ in range(count)))
        return metrics.stages["research"]
    first, second = await asyncio.gather(run(3), run(2))
    assert first.requests == {"serper": 3}
    assert second.requests == {"serper": 2}
    assert first.latency_seconds >= 0


def test_tokens_cost_and_missing_usage():
    metrics = RunMetrics()
    with metrics.stage("generate") as stage:
        record_request("gemini")
        record_usage(SimpleNamespace(usage_metadata=SimpleNamespace(
            prompt_token_count=100, candidates_token_count=40, thoughts_token_count=10,
        )), input_rate=1, output_rate=2)
    assert stage.input_tokens == 100
    assert stage.output_tokens == 50
    assert stage.estimated_cost_usd == pytest.approx(0.0002)
    with metrics.stage("missing") as missing:
        record_usage(SimpleNamespace(), input_rate=1, output_rate=2)
    assert missing.estimated_cost_usd is None
    assert missing.usage_missing_requests == 1


def test_stage_failure_and_context_reset():
    metrics = RunMetrics()
    with pytest.raises(ValueError), metrics.stage("failed"):
        record_request("gemini")
        raise ValueError("no response")
    record_request("outside")
    assert metrics.stages["failed"].status == "failed"
    assert metrics.stages["failed"].requests == {"gemini": 1}
    assert metrics.stages["failed"].estimated_cost_usd is None
