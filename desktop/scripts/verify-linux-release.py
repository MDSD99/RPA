"""Compare a built Linux archive with the current shared UI and runner sources."""

import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from PyInstaller.archive.readers import CArchiveReader

root = Path(__file__).resolve().parents[2]
desktop = root / "desktop"
archive = (
    Path(sys.argv[1]) if len(sys.argv) > 1 else desktop / "release/multirpa-desktop-0.1.0.tar.gz"
)
node = shutil.which("node")
if node is None:
    node = str(desktop / ".tools/node-v24.21.0-linux-x64/bin/node")

with tempfile.TemporaryDirectory(prefix="multirpa-release-verify-") as temporary:
    directory = Path(temporary)
    with tarfile.open(archive, "r:gz") as package:
        for relative in ["resources/app.asar", "resources/runner/multirpa-runner"]:
            matches = [
                entry for entry in package.getmembers() if entry.name.endswith("/" + relative)
            ]
            if len(matches) != 1 or not matches[0].isfile():
                raise RuntimeError("Archive lacks the expected application or native runner")
            source = package.extractfile(matches[0])
            assert source is not None
            with (directory / Path(relative).name).open("wb") as output:
                shutil.copyfileobj(source, output)
    subprocess.run([node, str(desktop / "scripts/prepare-renderer.cjs")], cwd=root, check=True)
    subprocess.run(
        [
            node,
            str(desktop / "scripts/verify-asar.cjs"),
            str(directory / "app.asar"),
        ],
        cwd=root,
        check=True,
    )
    pyz = CArchiveReader(str(directory / "multirpa-runner")).open_embedded_archive("PYZ.pyz")
    required = {
        "app.desktop.runner",
        "app.desktop.license",
        "app.secrets.domain",
        "app.adapters.mail_automation",
        "app.workflows.flow_engine",
        "app.workflows.flow_transfer",
    }
    assert required <= set(pyz.toc)
    count = 0
    for module in pyz.toc:
        if not module.startswith("app."):
            continue
        source = root / Path(module.replace(".", "/")).with_suffix(".py")
        if not source.exists():
            source = root / module.replace(".", "/") / "__init__.py"
        if not source.exists():
            if (root / module.replace(".", "/")).is_dir() and pyz.extract(module) is None:
                continue  # An existing namespace package has no bytecode file.
            raise RuntimeError("Bundled application module has no matching source")
        code = pyz.extract(module)
        expected = compile(
            source.read_bytes(), code.co_filename, "exec", dont_inherit=True, optimize=0
        )
        if code != expected:
            raise RuntimeError(f"Bundled application bytecode is stale: {module}")
        count += 1
    assert not any(module.startswith(("sqlalchemy", "psycopg", "fastapi")) for module in pyz.toc)
    print(
        f"Native runner bytecode matches {count} current app modules; "
        "central DB/API packages absent."
    )

with archive.open("rb") as package:
    digest = hashlib.file_digest(package, "sha256").hexdigest()
print(json.dumps({"filename": archive.name, "sha256": digest, "size": archive.stat().st_size}))
