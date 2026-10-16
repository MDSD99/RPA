"""Persistent portal management must not stop another process or reuse a stale PID."""

from scripts import portal_manage


def test_portal_identity_requires_pid_start_time_cwd_and_exact_command(monkeypatch):
    manifest = {"pid": 12345, "port": 8770, "start_time": "123"}
    good = {
        "cwd": str(portal_manage.PROJECT_ROOT),
        "command": portal_manage.command(8770),
        "start_time": "123",
    }
    monkeypatch.setattr(portal_manage.manage, "process_identity", lambda _pid: good)
    assert portal_manage.owned(manifest)
    for field, value in (
        ("cwd", "/other/project"),
        ("command", ["other-service"]),
        ("start_time", "124"),
    ):
        changed = {**good, field: value}
        monkeypatch.setattr(portal_manage.manage, "process_identity", lambda _pid, row=changed: row)
        assert not portal_manage.owned(manifest)


def test_missing_process_is_not_owned(monkeypatch):
    monkeypatch.setattr(portal_manage.manage, "process_identity", lambda _pid: None)
    assert not portal_manage.owned({"pid": 12345, "port": 8770, "start_time": "123"})
