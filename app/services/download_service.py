"""Serve only published desktop artifacts that pin this installation's license key."""

import json
import re
from pathlib import Path

from app.core.exceptions import DatabaseOperationError, NotFoundError


class DownloadService:
    def __init__(
        self, directory: Path | None, *, public_url: str, public_key: str, app_env: str
    ) -> None:
        self.directory = directory
        self.public_url, self.public_key, self.app_env = public_url.rstrip("/"), public_key, app_env

    def manifest(self) -> list[dict[str, object]]:
        if self.directory is None:
            return []
        path = self.directory / "manifest.json"
        if not path.exists():
            return []
        try:
            if path.is_symlink() or path.stat().st_size > 16384:
                raise ValueError
            data = json.loads(path.read_text(encoding="utf-8"))
            if (
                data["schema"] != 1
                or data["server_url"].rstrip("/") != self.public_url
                or data["server_public_key"] != self.public_key
                or data["channel"] not in {"development", "production"}
                or (self.app_env == "production" and data["channel"] != "production")
                or not isinstance(data["items"], list)
                or len(data["items"]) > 2
            ):
                raise ValueError
            seen = set()
            for item in data["items"]:
                filename = item["filename"]
                if (
                    item["platform"] not in {"linux", "windows"}
                    or item["platform"] in seen
                    or not isinstance(filename, str)
                    or not re.fullmatch(
                        r"MultiRPA-[a-zA-Z0-9_.-]{1,100}\.(?:tar\.gz|exe)", filename
                    )
                    or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
                    or not re.fullmatch(r"\d+\.\d+\.\d+(?:-[a-zA-Z0-9.-]+)?", item["version"])
                    or type(item["size"]) is not int
                    or not 0 < item["size"] <= 512 * 1024**2
                ):
                    raise ValueError
                artifact = self.directory / filename
                if (
                    artifact.is_symlink()
                    or not artifact.is_file()
                    or artifact.stat().st_size != item["size"]
                ):
                    raise ValueError
                seen.add(item["platform"])
            return data["items"]
        except (KeyError, ValueError, TypeError, OSError, AttributeError):
            raise DatabaseOperationError("Desktop release metadata is unavailable") from None

    def list(self) -> dict[str, object]:
        return {
            "items": [
                {
                    "platform": item["platform"],
                    "version": item["version"],
                    "size": item["size"],
                    "sha256": item["sha256"],
                    "url": f"/download/{item['platform']}",
                }
                for item in self.manifest()
            ]
        }

    def artifact(self, platform: str) -> Path:
        if self.directory is None:
            raise NotFoundError("Desktop release is unavailable")
        for item in self.manifest():
            if item["platform"] == platform:
                return self.directory / str(item["filename"])
        raise NotFoundError("Desktop release is unavailable")
