"""Release pin and filesystem checks prevent incompatible or unsafe downloads."""

import json

import pytest

from app.core.exceptions import DatabaseOperationError, NotFoundError
from app.services.download_service import DownloadService


@pytest.fixture
def release(tmp_path):
    filename = "MultiRPA-0.1.0-linux-x64.tar.gz"
    (tmp_path / filename).write_bytes(b"fixture artifact")
    data = {
        "schema": 1,
        "channel": "development",
        "server_url": "http://127.0.0.1:8770",
        "server_public_key": "a" * 43,
        "items": [
            {
                "platform": "linux",
                "filename": filename,
                "version": "0.1.0",
                "size": 16,
                "sha256": "a" * 64,
            }
        ],
    }
    service = DownloadService(
        tmp_path,
        public_url=data["server_url"],
        public_key=data["server_public_key"],
        app_env="development",
    )
    return tmp_path, data, service


def store(path, data):
    (path / "manifest.json").write_text(json.dumps(data))


def test_release_returns_only_public_metadata_and_fixed_artifact(release):
    directory, data, service = release
    store(directory, data)
    item = service.list()["items"][0]
    assert item["url"] == "/download/linux" and "filename" not in item
    assert service.artifact("linux").read_bytes() == b"fixture artifact"
    with pytest.raises(NotFoundError):
        service.artifact("windows")


@pytest.mark.parametrize(
    "damage", ["url", "key", "traversal", "symlink", "size", "duplicate", "channel"]
)
def test_release_fails_closed_on_wrong_pin_or_unsafe_artifact(release, damage):
    directory, data, service = release
    if damage == "url":
        data["server_url"] = "https://attacker.example.com"
    elif damage == "key":
        data["server_public_key"] = "b" * 43
    elif damage == "traversal":
        data["items"][0]["filename"] = "../private.env"
    elif damage == "symlink":
        artifact = directory / data["items"][0]["filename"]
        artifact.unlink()
        artifact.symlink_to(directory / "outside")
    elif damage == "size":
        data["items"][0]["size"] = 1
    elif damage == "duplicate":
        data["items"].append(dict(data["items"][0]))
    elif damage == "channel":
        service.app_env = "production"
    store(directory, data)
    with pytest.raises(DatabaseOperationError):
        service.list()


def test_missing_manifest_and_test_isolation_have_no_downloads(tmp_path):
    for directory in (None, tmp_path):
        service = DownloadService(
            directory, public_url="https://app.example.com", public_key="a" * 43, app_env="test"
        )
        assert service.list() == {"items": []}
        with pytest.raises(NotFoundError):
            service.artifact("linux")
