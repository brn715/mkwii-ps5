"""Link and sign the game executable (Build-GameExecutable.ps1).

Runs the link audit, snapshots every input, links the ELF, signs it into
eboot.bin and verifies the signed container's integrity. Output goes into a
timestamped artifacts/game-executable/<stamp> folder, so a rebuild never
overwrites a previous executable.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import libc_stub  # noqa: E402
import link_manifest  # noqa: E402
from common import REPO, die, file_sha256_upper, read_json, write_json  # noqa: E402


def stamp() -> str:
    now = time.time()
    return time.strftime("%Y%m%d-%H%M%S", time.localtime(now)) + f"-{int((now % 1) * 1000):03d}"


def main(argv: list[str]) -> int:
    game_build = Path(argv[1]) if len(argv) > 1 else REPO / "artifacts" / "game-build"

    # The link audit builds the stubs and the manifest in artifacts/game-link.
    if link_manifest.main([sys.argv[0], str(game_build)]) != 0:
        return 1

    audit_dir = REPO / "artifacts" / "game-link"
    audit = read_json(audit_dir / "result.json")
    if audit["unresolved"] or audit["skippedMembers"]:
        die("game dependencies are incomplete; inspect artifacts/game-link/unresolved.txt")

    out = REPO / "artifacts" / "game-executable" / stamp()
    inputs = out / "inputs"
    inputs.mkdir(parents=True)
    manifest = read_json(audit_dir / "manifest.json")

    # Snapshot every input and record its source hash, as Build-GameExecutable
    # does, so the executable is reproducible from the recorded evidence.
    snapshot_paths = {"objects": [], "archives": [], "stubs": []}
    hashes = []
    index = 0
    for kind in ("objects", "archives", "stubs"):
        for source in manifest[kind]:
            source_path = Path(source)
            snapshot = inputs / f"{index}-{source_path.name}"
            index += 1
            shutil.copyfile(source_path, snapshot)
            snapshot_paths[kind].append(str(snapshot))
            hashes.append({"source": source, "snapshot": str(snapshot),
                           "sha256": file_sha256_upper(snapshot)})
    write_json(out / "manifest.json", snapshot_paths)
    write_json(out / "inputs.json", hashes)

    dotnet = libc_stub.dotnet()
    elf = out / "game.elf"
    result = out / "result.json"
    cmd = [dotnet, "run", "--project", str(REPO / "ps5" / "tools" / "game_link"),
           "-c", "Release", "--", str(out / "manifest.json"), str(result), str(elf)]
    if subprocess.run(cmd, cwd=str(REPO)).returncode != 0:
        die(f"game executable link failed; inspect {out}")
    if not elf.exists():
        die("link produced no game.elf")

    bindgen = libc_stub.bindgen()
    eboot = out / "eboot.bin"
    if subprocess.run([dotnet, "--roll-forward", "LatestMajor", str(bindgen),
                       "self", "--sign", "--in", str(elf), "--out", str(eboot)],
                      cwd=str(REPO)).returncode != 0:
        die("game SELF signature failed")

    verify = subprocess.run([dotnet, "--roll-forward", "LatestMajor", str(bindgen),
                             "self", "--inspect", "--file", str(eboot)],
                            cwd=str(REPO), capture_output=True, text=True)
    if verify.returncode != 0 or "Integrity: ok" not in verify.stdout:
        die("game SELF integrity verification failed")

    resolved = read_json(result)
    write_json(out / "build.json", {
        "kind": "Mario Kart Wii base runtime executable",
        "linkedGame": True, "nativeExecution": False, "packaged": False,
        "gameElfSha256": file_sha256_upper(elf),
        "ebootSha256": file_sha256_upper(eboot),
        "includedObjects": resolved["includedObjects"],
        "unresolved": 0, "inputManifest": str(out / "inputs.json"),
    })
    print(f"executable: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
