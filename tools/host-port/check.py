"""Validate every prerequisite the macOS build needs and print the plan.

This is the implementation of ``tools/build-macos.sh --check``. It never builds
anything: it confirms the host tools, the fetched sources, the game assets and
their expected hashes, then prints the ordered step list the full build would
run. Missing prerequisites are collected and reported together; the process
exits non-zero if any remain.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import REPO, file_sha256_upper, read_json  # NOQA: E402

DOL_SHA256 = "80d18895b39c63bd80f457398bfcbb91b7d16ac116a41a88967e954080155b05"
REL_SHA256 = "16d9d146112541fefea701ecb5bc1a496f9d50e4a752fbb5b6778e7c6399f67d"

STEPS = [
    ("fetch", "sources: clone pinned Wiicompiled/ps5link-sdk/SharpProspero at "
              "sources.lock.json commits and apply patches/"),
    ("fetch", "llvm-mingw: fetch the pinned 20260908 macOS-universal archive"),
    ("fetch", "game-deps: fetch + verify SDL3, ImGui, xxHash, fmt, Tracy"),
    ("fetch", "cxx-toolchain: fetch LLVM 18.1.8 sources, cmake helpers and the "
              "FreeBSD 9.3 C headers"),
    ("fetch", "desktop-check: fetch + verify Dawn/Abseil headers"),
    ("build", "libc++: configure ps5/toolchain/libcxx and build cxx_static "
              "(artifacts/libcxx-build)"),
    ("build", "translator: dotnet build Translator.Cli (Release net8.0)"),
    ("build", "translate: translate-recursive, generate-data-init, "
              "emit-base-manifest, emit-build-shards"),
    ("build", "compile game: configure artifacts/game-build and build every "
              "translated + platform target"),
    ("build", "compile runtime: build mkw_ps5_runtime + dependencies into "
              "artifacts/runtime-build"),
    ("build", "SDL input + audio: build libSDL3.a into artifacts/sdl-input and "
              "artifacts/sdl-audio"),
    ("build", "shaders: build vertex (--varyings), TEV fragment and copy "
              "shaders into artifacts/gx-varyings, tev-fragment, gx-copy-shader"),
    ("link", "audit: assemble artifacts/game-link manifest, libc.a and posix.o"),
    ("link", "link + sign: game_link -> game.elf -> sharpprospero self --sign -> "
             "eboot.bin"),
    ("package", "assemble the PPSA99611 development package under "
                "artifacts/game-package"),
]


class Checker:
    def __init__(self):
        self.errors: list[str] = []
        self.checks: list[str] = []

    def ok(self, message: str):
        self.checks.append(message)

    def fail(self, message: str):
        self.errors.append(message)

    def require_path(self, path: Path, label: str | None = None, executable: bool = False):
        label = label or str(path)
        if not path.exists():
            self.fail(f"missing {label}: {path}")
            return False
        if executable and not path.stat().st_mode & 0o111:
            self.fail(f"not executable: {path}")
            return False
        self.ok(label)
        return True

    def require_dir(self, path: Path, label: str | None = None):
        label = label or str(path)
        if not path.is_dir():
            self.fail(f"missing {label}: {path}")
            return False
        self.ok(label)
        return True


def tool_version(cmd, pattern: str, minimum=None):
    """Return (found_string, ok) after matching --version output to ``pattern``."""
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, str(exc)
    text = out.stdout + out.stderr
    match = re.search(pattern, text)
    return (match.group(1) if match else None), text


def check_host_tools(c: Checker) -> None:
    # dotnet >= 10
    dotnet = shutil.which("dotnet")
    if not dotnet:
        c.fail("dotnet not found on PATH (need the .NET 10 SDK)")
    else:
        version, text = tool_version([dotnet, "--version"], r"(\d+\.\d+\.\d+)")
        if not version:
            c.fail(f"cannot read dotnet version: {text.strip()[:120]}")
        else:
            major = int(version.split(".")[0])
            if major < 10:
                c.fail(f"dotnet {version} found, need >= 10 (the SDK builds the "
                       f"tools; the translator runs on net8.0 via --roll-forward)")
            else:
                c.ok(f"dotnet {version}")
    for tool in ("cmake", "ninja", "python3", "git"):
        found = shutil.which(tool)
        if not found:
            c.fail(f"{tool} not found on PATH")
        else:
            c.ok(f"{tool}: {found}")


def check_llvm18(c: Checker) -> None:
    prefix = Path("/opt/homebrew/opt/llvm@18/bin")
    if not prefix.is_dir():
        c.fail("LLVM 18.1.8 not found at /opt/homebrew/opt/llvm@18 "
               "(brew install llvm@18, or set MKW_LLVM18_BIN)")
        return
    for tool in ("llvm-mc", "llvm-objcopy", "clang", "llc"):
        path = prefix / tool
        if not path.exists():
            c.fail(f"missing shader tool: {path}")
    mc = prefix / "llvm-mc"
    if mc.exists():
        _, text = tool_version([str(mc), "--version"], r"LLVM version (\S+)")
        if "18.1.8" not in text:
            c.fail(f"{mc} is not LLVM 18.1.8")
        else:
            c.ok("llvm-mc reports LLVM 18.1.8")


def check_fetched(c: Checker) -> None:
    tools = REPO / ".tools"
    mingw = list(tools.glob("llvm-mingw-*macos*"))
    if not mingw:
        mingw = list(tools.glob("llvm-mingw-*"))
    if not mingw:
        c.fail("llvm-mingw not fetched under .tools (run the fetch step)")
    else:
        c.require_path(mingw[0] / "bin" / "clang", "llvm-mingw clang")
    c.require_dir(tools / "ps5-sysroot" / "usr" / "include",
                  ".tools/ps5-sysroot/usr/include (FreeBSD C headers)")
    cxx = tools / "cxx-src"
    for name in ("libcxx-18.1.8.src", "libcxxabi-18.1.8.src",
                 "libunwind-18.1.8.src", "cmake-18.1.8.src"):
        c.require_dir(cxx / name, f".tools/cxx-src/{name}")
    c.require_path(cxx / "runtimes" / "cmake" / "Modules" / "HandleFlags.cmake",
                   "cxx-src runtimes/cmake/Modules/HandleFlags.cmake")
    c.require_dir(tools / "game-deps", ".tools/game-deps")
    for dep in read_json(REPO / "ps5" / "toolchain" / "game-dependencies.json"):
        c.require_dir(tools / "game-deps" / dep["directory"],
                      f".tools/game-deps/{dep['directory']}")
    c.require_path(tools / "console" / "libc.prx",
                   ".tools/console/libc.prx (signed, user-supplied)")
    c.require_dir(tools / "desktop-check" / "abseil-cpp-20240722.0",
                  ".tools/desktop-check/abseil-cpp-20240722.0")


def check_dependencies(c: Checker) -> None:
    lock = read_json(REPO / "sources.lock.json")
    for dep in lock["repositories"]:
        dest = REPO / dep["directory"]
        if not (dest / ".git").exists():
            c.fail(f"dependency {dep['directory']} not cloned; run tools/Get-Sources.ps1 "
                   f"or the fetch step")
            continue
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(dest),
                              capture_output=True, text=True)
        if head.stdout.strip() != dep["commit"]:
            c.fail(f"{dep['directory']} is at {head.stdout.strip()}, expected "
                   f"{dep['commit']}")
        else:
            c.ok(f"{dep['directory']} @ {dep['commit'][:12]}")


def check_game_assets(c: Checker) -> str | None:
    assets = REPO / "Wiicompiled" / "Assets"
    dol = assets / "main.dol"
    rel = assets / "StaticR.rel"
    recomp = REPO / "Wiicompiled" / "projects" / "mkwii" / "recomp.yml"
    if not recomp.exists():
        c.fail(f"missing recomp.yml: {recomp}")
        return None
    text = recomp.read_text(encoding="utf-8")
    m = re.search(r"sha256:\s*([0-9a-f]{64})", text)
    expected_dol = m.group(1) if m else DOL_SHA256
    # The REL hash is the second sha256 in the inputs block.
    matches = re.findall(r"sha256:\s*([0-9a-f]{64})", text)
    expected_rel = matches[1] if len(matches) > 1 else REL_SHA256
    for label, path, expected in (("main.dol", dol, expected_dol),
                                  ("StaticR.rel", rel, expected_rel)):
        if not path.exists():
            c.fail(f"missing game asset Wiicompiled/Assets/{label}; run "
                   f"ps5/Prepare-Game.ps1 -DiscImage <PAL image>")
            continue
        actual = file_sha256_upper(path)
        if actual.lower() != expected.lower():
            c.fail(f"{label} hash {actual} does not match the expected "
                   f"{expected.upper()} (wrong game revision)")
        else:
            c.ok(f"{label} @ {expected[:16]}...")
    return str(rel)


def check_kernel_exports(c: Checker) -> None:
    if not (REPO / "artifacts" / "kernel-exports" / "result.json").exists():
        c.fail("missing artifacts/kernel-exports/result.json; capture kernel exports "
               "from the console before generating the POSIX stub")
    else:
        c.ok("artifacts/kernel-exports/result.json")


def print_plan(c: Checker) -> None:
    print()
    print("Planned build steps (tools/build-macos.sh):")
    for phase, step in STEPS:
        print(f"  [{phase:7}] {step}")


def main() -> int:
    c = Checker()
    print("== checking macOS build prerequisites ==")
    print()
    print("[host tools]")
    check_host_tools(c)
    print("[shader toolchain]")
    check_llvm18(c)
    print("[fetched sources]")
    check_fetched(c)
    print("[dependency repositories]")
    # Only meaningful once .tools exists; keep it before asset checks so the
    # report reads top-down the way the build runs.
    check_dependencies(c)
    print("[game assets]")
    check_game_assets(c)
    print("[kernel exports]")
    check_kernel_exports(c)

    print()
    for line in c.checks:
        print(f"  ok   {line}")
    print_plan(c)
    print()
    if c.errors:
        print(f"FAILED: {len(c.errors)} prerequisite(s) missing:")
        for line in c.errors:
            print(f"  !!   {line}")
        return 1
    print(f"All {len(c.checks)} prerequisites satisfied. "
          "This environment can run tools/build-macos.sh.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
