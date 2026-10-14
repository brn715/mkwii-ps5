"""Assemble the game link manifest and run the audit (Inspect-GameLink.ps1).

Collects the runtime objects, the platform objects, every translated object,
the runtime archives (including SDL3 audio and Aurora Abseil), then builds the
two import stubs and writes artifacts/game-link/manifest.json. The game_link
audit resolves the closure and reports unresolved symbols, demangled with
llvm-cxxfilt exactly as the PowerShell script does.
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import libc_stub  # noqa: E402
from common import REPO, die, read_json, write_json  # noqa: E402

RUNTIME_ARCHIVE_NAMES = [
    "pugixml", "imgui", "cryptopp", "xxhash", "fmt", "texture_decode",
    "agc_texture", "gx_frontend", "gx_registers", "gx_textures", "gx_tev",
    "gx_material", "gx_geometry", "gx_viewport", "gx_copy_commands",
    "aurora_input", "native_input", "virtual_pad",
]
PLATFORM_TARGETS = [
    "host_context", "guest_memory", "guest_flat", "engine_memory",
    "cxx_platform", "cpu_baseline", "game_data",
]


def collect(runtime_build: Path, game_build: Path) -> dict:
    objects: list[Path] = []
    runtime_objs = sorted((runtime_build / "CMakeFiles" / "mkw_ps5_runtime.dir").rglob("*.o"))
    graph = read_json(runtime_build / "runtime-graph.json")
    if len(runtime_objs) != graph["translationUnits"]:
        die(f"expected {graph['translationUnits']} runtime objects, got {len(runtime_objs)}; "
            f"compile the full runtime first")
    objects += runtime_objs

    for target in PLATFORM_TARGETS:
        found = sorted((game_build / "CMakeFiles" / f"mkw_ps5_{target}.dir").rglob("*.o"))
        if not found:
            found = sorted((runtime_build / "CMakeFiles" / f"mkw_ps5_{target}.dir").rglob("*.o"))
        if not found:
            die(f"missing platform target {target}; compile the game and runtime first")
        objects += found

    translated = sorted((game_build / "CMakeFiles" / "mkw_ps5_translated.dir").rglob("*.o"))
    game_graph = read_json(game_build / "game-graph.json")
    if len(translated) != game_graph["translationUnits"]:
        die(f"expected {game_graph['translationUnits']} translated objects, got "
            f"{len(translated)}; compile the game first")
    objects += translated

    fprof = sorted((game_build / "CMakeFiles" / "mkw_ps5_fprof.dir").rglob("*.o"))
    objects += fprof

    archives = [REPO / "artifacts" / "libcxx-build" / "lib" / "libc++.a"]
    for name in RUNTIME_ARCHIVE_NAMES:
        archives.append(runtime_build / f"libmkw_ps5_{name}.a")
    audio = read_json(REPO / "artifacts" / "sdl-audio" / "result.json")
    audio_archive = Path(audio["archive"])
    if not audio["compiled"] or hashlib.sha256(audio_archive.read_bytes()).hexdigest().upper() \
            != audio["sha256"].upper():
        die("rebuild the native SDL audio archive")
    archives.append(audio_archive)
    archives += sorted((runtime_build / "input" / "aurora" / "abseil").rglob("*.a"))

    for archive in archives:
        if not archive.exists():
            die(f"missing archive: {archive}; build SDL input and the runtime first")
    return {"objects": objects, "archives": archives}


def write_posix_stub(output: Path) -> None:
    kernel = REPO / "artifacts" / "kernel-exports" / "result.json"
    if not kernel.exists():
        die("missing artifacts/kernel-exports/result.json; capture the kernel exports first")
    if Path(str(output) + ".kernel.o").exists():
        die(f"refusing to overwrite existing {output}.kernel.o")
    cmd = [libc_stub.dotnet(), "run", "--project",
           str(REPO / "ps5" / "tools" / "posix_stub"), "-c", "Release",
           "--", str(kernel), str(output)]
    if subprocess.run(cmd, cwd=str(REPO)).returncode != 0:
        die("POSIX import candidate generation failed")


def demangle(result: Path, out: Path) -> None:
    report = read_json(result)
    unresolved = report.get("unresolved", [])
    if not unresolved:
        out.unlink(missing_ok=True)
        return
    cxxfilt = libc_stub.llvm_tool("llvm-cxxfilt")
    proc = subprocess.run([str(cxxfilt)], input="\n".join(unresolved) + "\n",
                          capture_output=True, text=True)
    out.write_text(proc.stdout, encoding="utf-8")


def main(argv: list[str]) -> int:
    runtime_build = REPO / "artifacts" / "runtime-build"
    game_build = Path(argv[1]) if len(argv) > 1 else REPO / "artifacts" / "game-build"
    out = REPO / "artifacts" / "game-link"
    out.mkdir(parents=True, exist_ok=True)

    manifest = collect(runtime_build, game_build)
    objects = manifest["objects"]
    archives = manifest["archives"]

    libc = out / "libc.a"
    if libc.exists():
        libc.unlink()
    rc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parent / "libc_stub.py"),
         str(REPO / ".tools" / "console" / "libc.prx"), str(libc)]
        + [str(p) for p in objects + archives])
    if rc.returncode != 0:
        die("verified libc stub generation failed")
    write_posix_stub(out / "posix.o")

    paths = {"objects": [str(p) for p in objects],
             "archives": [str(p) for p in archives],
             "stubs": [str(libc), str(out / "posix.o"), str(out / "posix.o.kernel.o")]}
    write_json(out / "manifest.json", paths)

    result = out / "result.json"
    if result.exists():
        result.unlink()
    dotnet = libc_stub.dotnet()
    cmd = [dotnet, "run", "--project", str(REPO / "ps5" / "tools" / "game_link"),
           "-c", "Release", "--", str(out / "manifest.json"), str(result)]
    if subprocess.run(cmd, cwd=str(REPO)).returncode != 0:
        die("game link audit failed")
    demangle(result, out / "unresolved.txt")
    audit = read_json(result)
    print(f"link audit: {audit['includedObjects']} objects, "
          f"{len(audit['unresolved'])} unresolved, {len(audit['skippedMembers'])} skipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
