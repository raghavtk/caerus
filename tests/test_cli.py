from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

from cli import app
from schemas.models import ApplicationPackage, CompanyBrief, ParsedJD


def test_cli_run_owns_async_boundary_and_preserves_summary():
    package = ApplicationPackage(jd=ParsedJD(company='Acme', role='Engineer'), company_brief=CompanyBrief(company='Acme'))
    with patch('cli.orchestrate', new_callable=AsyncMock, return_value=package) as orchestrate:
        result = CliRunner().invoke(app, ['run', 'job text', '--no-notion'])
    assert result.exit_code == 0, result.output
    assert 'Acme' in result.output
    assert 'Engineer' in result.output
    orchestrate.assert_awaited_once_with('job text', skip_notion=True)


def test_cli_resume_uses_checkpoint_without_prompting(tmp_path):
    path = tmp_path / "state.json"
    package = ApplicationPackage(jd=ParsedJD(company="Saved Acme"), checkpoint_path=str(path))
    with patch("cli.orchestrate", new_callable=AsyncMock, return_value=package) as orchestrate:
        result = CliRunner().invoke(app, ["run", "--resume", str(path), "--no-notion"])
    assert result.exit_code == 0, result.output
    assert "Saved Acme" in result.output
    assert "Paste job" not in result.output
    orchestrate.assert_awaited_once_with(skip_notion=True, resume_from=path)


def test_cli_rejects_input_and_resume_before_running():
    with patch("cli.orchestrate", new_callable=AsyncMock) as orchestrate:
        result = CliRunner().invoke(app, ["run", "another job", "--resume", "state.json"])
    assert result.exit_code == 2
    assert "not both" in result.output
    orchestrate.assert_not_called()


def test_cli_failure_prints_resume_instruction():
    error = RuntimeError("PDF conversion failed")
    error.add_note('Resume with: caerus run --resume "saved/state.json"')
    with patch("cli.orchestrate", new_callable=AsyncMock, side_effect=error):
        result = CliRunner().invoke(app, ["run", "job"])
    assert result.exit_code == 1
    assert "PDF conversion failed" in result.output
    assert 'caerus run --resume "saved/state.json"' in result.output
