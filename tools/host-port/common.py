"""Shared helpers for the macOS/Linux bash build pipeline.

Every other helper and tools/build-macos.sh import this. It mirrors the exact
checks the PowerShell scripts perform (checksum verification, refusal to
overwrite, hash reports) without depending on PowerShell.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def file_sha256_upper(path: Path) -> str:
    """The PowerShell Get-FileHash spelling: uppercase hex, no prefix."""
    return sha256(path).upper()


class Fail(SystemExit):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def die(message: str) -> "NoReturn":  # type: ignore[name-defined]
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def run(cmd, cwd: Path | None = None, env: dict | None = None, quiet: bool = False):
    """Run a command, echoing it; abort with the same code on failure."""
    if not quiet:
        print("+ " + " ".join(str(c) for c in cmd), flush=True)
    proc = subprocess.run([str(c) for c in cmd], cwd=str(cwd) if cwd else None, env=env)
    if proc.returncode != 0:
        die(f"command failed ({proc.returncode}): {' '.join(str(c) for c in cmd)}")
    return proc.returncode


def download(url: str, dest: Path, expected_sha256: str) -> None:
    """Fetch ``url`` to ``dest`` unless present; always verify the checksum.

    Matches Fetch() in the PowerShell scripts: never re-download an existing
    file, but never trust one either.
    """
    if dest.exists():
        if file_sha256_upper(dest) != expected_sha256.upper():
            die(f"checksum mismatch: {dest}")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".partial")
    print(f"+ curl -L --fail {url} -> {dest}", flush=True)
    try:
        with urllib.request.urlopen(url, timeout=300) as response, open(partial, "wb") as out:
            while True:
                chunk = response.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
    except Exception as exc:  # noqa: BLE001 - report the URL with the failure
        partial.unlink(missing_ok=True)
        die(f"download failed: {url}: {exc}")
    if file_sha256_upper(partial) != expected_sha256.upper():
        partial.unlink(missing_ok=True)
        die(f"checksum mismatch after download: {dest}")
    partial.replace(dest)


def extract(archive: Path, dest: Path, members: list[str] | None = None,
            materialize_symlinks: bool = False) -> None:
    """Extract a tar archive.

    ``members`` restricts extraction to those names (as written in the tar).
    ``materialize_symlinks`` copies the target of each relative symlink into
    the link path, which is what Prepare-CxxToolchain.ps1 does on hosts that
    do not create Unix symlinks. macOS *can* create them, but the FreeBSD
    archive's links point at targets that are themselves extracted in the same
    pass, so the same two-phase copy keeps the tree identical on every host.
    """
    dest.mkdir(parents=True, exist_ok=True)
    want = None if members is None else set(members)
    links: list[tuple[str, str]] = []
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            if want is not None and member.name not in want and not any(
                member.name.startswith(w.rstrip("/") + "/") for w in want):
                continue
            if member.issym() or member.islnk():
                target = member.linkname
                if member.issym() and not os.path.isabs(target):
                    parent = os.path.dirname(member.name)
                    resolved = os.path.normpath(os.path.join(parent, target))
                elif member.islnk():
                    resolved = target
                else:
                    # Absolute symlink: skip; nothing under the sysroot points out.
                    continue
                links.append((member.name, resolved))
                continue
            # The FreeBSD archive stores read-only (0444) files. Re-extracting
            # over an existing copy must make the target writable first, or the
            # open(...,"wb") fails even though we own the directory.
            target = dest / member.name
            if target.exists():
                try:
                    target.chmod(target.stat().st_mode | 0o200)
                except OSError:
                    pass
            tar.extract(member, dest)
    if not materialize_symlinks:
        return
    root = dest.resolve()
    for link_name, resolved in links:
        destination = (dest / link_name).resolve()
        source = (dest / resolved).resolve()
        if not (str(destination).startswith(str(root)) and str(source).startswith(str(root))):
            die(f"header link escapes the extraction root: {link_name}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not source.exists():
            die(f"cannot materialize {link_name}: target {resolved} absent")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            try:
                destination.chmod(destination.stat().st_mode | 0o200)
            except OSError:
                pass
        destination.write_bytes(source.read_bytes())


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_json_if_exists(path: Path):
    return read_json(path) if path.exists() else None


def newest_dir(parent: Path) -> Path | None:
    if not parent.is_dir():
        return None
    entries = [p for p in parent.iterdir() if p.is_dir()]
    return max(entries, key=lambda p: p.name) if entries else None
