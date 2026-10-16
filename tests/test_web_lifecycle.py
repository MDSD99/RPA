"""Resources must close even when startup or another shutdown callback fails."""

from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.web.api import create_app


def test_startup_failure_releases_engine_before_application_is_available(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    engine = Mock()
    monkeypatch.setattr("app.web.runtime.DatabaseSettings.from_env", lambda: object())
    monkeypatch.setattr("app.web.api.create_db_engine", lambda _settings: engine)
    monkeypatch.setattr(
        "app.web.runtime.create_session_factory",
        Mock(side_effect=RuntimeError("factory unavailable")),
    )

    with pytest.raises(RuntimeError, match="factory unavailable"):
        with TestClient(create_app(start_workers=False)):
            pytest.fail("A failed startup must not expose the application")

    engine.dispose.assert_called_once_with()


def test_shutdown_attempts_every_resource_when_scheduler_stop_fails(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    closed = []
    engine = Mock()
    engine.dispose.side_effect = lambda: closed.append("engine")
    monkeypatch.setattr("app.web.runtime.DatabaseSettings.from_env", lambda: object())
    monkeypatch.setattr("app.web.api.create_db_engine", lambda _settings: engine)
    monkeypatch.setattr("app.web.runtime.create_session_factory", lambda _engine: sessionmaker())
    monkeypatch.setattr("app.web.runtime.ManagementService.healthy", lambda _self: True)
    monkeypatch.setattr("app.web.runtime.DemoWorkerPool.stop", lambda _self: closed.append("demo"))
    monkeypatch.setattr(
        "app.web.runtime.WorkflowWorkerPool.stop", lambda _self: closed.append("workflow")
    )

    def fail_stop(_self):
        closed.append("scheduler")
        raise RuntimeError("scheduler stop failed")

    monkeypatch.setattr("app.web.runtime.SchedulerRunner.stop", fail_stop)
    with pytest.raises(RuntimeError, match="scheduler stop failed"):
        with TestClient(create_app(start_workers=False)) as client:
            assert client.get("/").status_code == 200

    assert closed == ["scheduler", "workflow", "demo", "engine"]
