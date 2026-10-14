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
