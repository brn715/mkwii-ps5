"""Verified libc import stub (New-VerifiedLibcStub.ps1).

Given a signed libc.prx and the objects/archives that will be linked, compute
the undefined symbols, translate each to its PS5 NID, keep only those the
module actually exports, and hand the surviving names to
sharpprospero-bindgen to emit the stub object. The audit report records every
candidate and whether it was verified, and generation is refused when no
candidate resolves.
"""
from __future__ import annotations

import base64
import hashlib
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import REPO, die, write_json  # noqa: E402

SALT = bytes([0x51, 0x8D, 0x64, 0xA6, 0x35, 0xDE, 0xD8, 0xC1,
              0xE6, 0xB0, 0x39, 0xB1, 0xC3, 0xE5, 0x52, 0x30])


def nid(name: str) -> str:
    digest = hashlib.sha1(name.encode("utf-8") + SALT).digest()
    short = digest[:8][::-1]
    return base64.b64encode(short).decode("ascii").rstrip("=").replace("/", "-")


def llvm_tool(name: str) -> Path:
    candidates = sorted((REPO / ".tools").glob(f"llvm-mingw-*/bin/{name}"))
    if not candidates:
        die(f"llvm-mingw {name} not found under .tools; run the fetch step")
    return candidates[0]


def dotnet() -> str:
    import shutil
    found = shutil.which("dotnet")
    if not found:
        die("dotnet not found on PATH")
    return found


def bindgen() -> Path:
    dll = (REPO / "SharpProspero" / "tools" / "SharpProspero.Bindings.Generator"
           / "bin" / "Release" / "net10.0" / "sharpprospero-bindgen.dll")
    if not dll.exists():
        die("build sharpprospero-bindgen first (Release net10.0)")
    return dll


def undefined_symbols(paths: list[Path], response: Path) -> list[str]:
    """Run llvm-nm over every input; return the case-sensitive unique U names."""
    nm = llvm_tool("llvm-nm")
    response.parent.mkdir(parents=True, exist_ok=True)
    response.write_text("\n".join(f'"{p.resolve().as_posix()}"' for p in paths),
                        encoding="utf-8")
    proc = subprocess.run([str(nm), "-u", "--format=posix", f"@{response}"],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        die(f"llvm-nm failed: {proc.stderr.strip()}")
    names = set()
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "U":
            names.add(parts[0])
    return sorted(names)


def module_exports(module: Path) -> set[str]:
    proc = subprocess.run(
        [dotnet(), "--roll-forward", "LatestMajor", str(bindgen()),
         "elf", "--file", str(module), "--exports"],
        capture_output=True, text=True)
    if proc.returncode != 0:
        die(f"cannot inspect module exports: {proc.stderr.strip()}")
    exports = set()
    for line in proc.stdout.splitlines():
        parts = line.strip().split()
        # Export rows look like:  <NID>  lib=3 mod=0 func libc
        if len(parts) >= 2 and parts[1].startswith("lib="):
            exports.add(parts[0])
    return exports


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        die("usage: libc_stub.py <module.prx> <output.a> <objects...>")
    module = Path(argv[1])
    output = Path(argv[2])
    paths = [Path(p) for p in argv[3:]]
    if not module.exists():
        die(f"module not found: {module}")
    if not paths:
        die("no objects or archives supplied")
    # These are regenerable audit intermediates, not user outputs: rewrite them
    # on every run so re-running the link stage is idempotent. (The .ps1 scripts
    # overwrite them too; only the final executable/package refuse to clobber.)
    for stale in (Path(str(output) + ".names.txt"),
                  output.with_suffix(output.suffix + ".audit.json")):
        if stale.exists():
            stale.unlink()

    exports = module_exports(module)
    report = []
    verified = []
    for name in undefined_symbols(paths, output.with_name(output.name + ".objects.rsp")):
        value = nid(name)
        exported = value in exports
        report.append({"symbol": name, "nid": value, "exported": exported})
        if exported:
            verified.append(name)
    if not verified:
        die("no verified exports for this stub")
    write_json(output.with_suffix(output.suffix + ".audit.json"), report)
    Path(str(output) + ".names.txt").write_text(
        "\n".join(verified) + "\n", encoding="utf-8")
    proc = subprocess.run(
        [dotnet(), "--roll-forward", "LatestMajor", str(bindgen()),
         "stub", "--module", str(module), "--names", f"{output}.names.txt",
         "--out", str(output)], capture_output=True, text=True)
    if proc.returncode != 0:
        die(f"verified libc stub generation failed: {proc.stderr.strip()}")
    print(f"libc stub: {len(verified)} verified exports of {len(report)} candidates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
