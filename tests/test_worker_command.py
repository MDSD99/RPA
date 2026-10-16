"""The standalone worker never opens a database without a registered workflow."""

import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.workflows.registry import WorkflowDefinition, WorkflowRegistry

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/worker.py"
SPEC = importlib.util.spec_from_file_location("rpa_worker_command", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
command = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(command)


@pytest.mark.parametrize("arguments,expected", [([], 2), (["--list"], 0)])
def test_empty_registry_never_opens_database(monkeypatch, capsys, arguments, expected):
    database = Mock(side_effect=AssertionError("Database must not be opened"))
    monkeypatch.setattr(command, "build_registry", WorkflowRegistry)
    monkeypatch.setattr(command, "create_db_engine", database)
    assert command.main(arguments) == expected
    database.assert_not_called()
    output = capsys.readouterr()
    assert "akış" in (output.out + output.err).lower()


def test_list_registered_names_needs_no_database_or_handler_execution(monkeypatch, capsys):
    handler = Mock(side_effect=AssertionError("Listing must not execute a workflow"))
    registry = WorkflowRegistry([WorkflowDefinition("test_flow", "Test", "Local test", handler)])
    database = Mock(side_effect=AssertionError("Database must not be opened"))
    monkeypatch.setattr(command, "build_registry", lambda: registry)
    monkeypatch.setattr(command, "create_db_engine", database)
    assert command.main(["--list"]) == 0
    assert capsys.readouterr().out.strip() == "test_flow"
    database.assert_not_called()
    handler.assert_not_called()


def test_registry_error_is_sanitized(monkeypatch, capsys):
    marker = "FAKE_PRIVATE_HANDLER_CONFIGURATION"
    monkeypatch.setattr(command, "build_registry", Mock(side_effect=RuntimeError(marker)))
    assert command.main([]) == 1
    output = capsys.readouterr()
    assert marker not in output.out + output.err
    assert "bastırıldı" in output.err
