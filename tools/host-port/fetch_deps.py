"""Fetch and verify every source the macOS build needs.

Consolidates the read-only preparation steps that the Windows pipeline runs as
separate PowerShell scripts, keeping each script's checks:

  Get-Sources.ps1            -> pinned dependency repositories + patches
  Prepare-GameDependencies   -> SDL3/ImGui/xxHash/fmt/Tracy sources
  Prepare-CxxToolchain       -> LLVM 18.1.8 sources, cmake helpers, FreeBSD headers
  Prepare-DesktopCheck       -> Dawn/Abseil headers (verified only, never linked)

One host-port addition has no PowerShell equivalent: the pinned llvm-mingw
release also ships a macOS-universal archive, and ps5-cxx.cmake now selects the
suffix-less compiler from it on non-Windows hosts, so this step fetches it too.

Everything is idempotent: existing archives are re-verified, existing source
trees are checked against their pinned commit, and an already-applied patch is
detected with the same reverse dry run Apply-SourcePatches.ps1 uses.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import REPO, die, download, extract, file_sha256_upper, read_json  # noqa: E402

# The macOS-universal build of the pinned llvm-mingw release. The Windows
# x86_64 archive is recorded in sources.lock.json; this is the same release and
# LLVM, chosen by ps5-cxx.cmake on a non-Windows host. Hash computed from the
# upstream asset for release 20260908.
LLVM_MINGW_MACOS = {
    "name": "llvm-mingw-20260908-ucrt-macos-universal",
    "url": "https://github.com/mstorsjo/llvm-mingw/releases/download/20260908/"
           "llvm-mingw-20260908-ucrt-macos-universal.tar.xz",
    "sha256": "d1dc5d1ecf3a3ced5ed5544c72f1acd0c8e84eb3024d520ecc6b143eec62a149",
}


def git(*args, cwd: Path):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)


def apply_sources() -> None:
    """Reconstruct the pinned dependency repositories (Get-Sources.ps1)."""
    lock = read_json(REPO / "sources.lock.json")
    tools = REPO / ".tools"
    tools.mkdir(exist_ok=True)
    for dep in lock["repositories"]:
        name = dep["directory"]
        dest = REPO / name
        patch = REPO / "patches" / f"{name}.patch"
        additions = REPO / "dependency-additions" / name
        if dest.exists():
            head = git("rev-parse", "HEAD", cwd=dest)
            if head.returncode != 0:
                die(f"{dest} exists but is not a git checkout; remove it for a fresh build")
            if head.stdout.strip() != dep["commit"]:
                die(f"{dest} is at {head.stdout.strip()}, expected {dep['commit']} "
                    f"(pinned dependency); remove it for a fresh build")
        else:
            print(f"+ git clone {dep['url']} {name}", flush=True)
            clone = git("clone", "--no-checkout", dep["url"], str(dest), cwd=REPO)
            if clone.returncode != 0:
                die(f"clone failed: {name}: {clone.stderr.strip()}")
            co = git("checkout", "--detach", dep["commit"], cwd=dest)
            if co.returncode != 0:
                die(f"checkout failed: {name}: {co.stderr.strip()}")
        if patch.exists() and patch.stat().st_size > 0:
            if git("apply", "--reverse", "--check", str(patch), cwd=dest).returncode == 0:
                print(f"  patch already applied: {name}", flush=True)
            else:
                check = git("apply", "--check", str(patch), cwd=dest)
                if check.returncode != 0:
                    die(f"patch does not apply cleanly: {patch}: {check.stderr.strip()}")
                if git("apply", str(patch), cwd=dest).returncode != 0:
                    die(f"patch failed: {patch}")
                print(f"  applied patch: {name}", flush=True)
        if additions.is_dir():
            for src in additions.rglob("*"):
                if not src.is_file():
                    continue
                rel = src.relative_to(additions)
                target = dest / rel
                if target.exists() and file_sha256_upper(target) != file_sha256_upper(src):
                    die(f"dependency addition would overwrite a different file: {target}")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, target)


def prepare_llvm_mingw() -> None:
    tools = REPO / ".tools"
    archive = tools / f"{LLVM_MINGW_MACOS['name']}.tar.xz"
    download(LLVM_MINGW_MACOS["url"], archive, LLVM_MINGW_MACOS["sha256"])
    if not (tools / LLVM_MINGW_MACOS["name"] / "bin" / "clang").exists():
        print(f"+ tar -xf {archive.name} -C .tools", flush=True)
        extract(archive, tools)


def prepare_game_dependencies() -> None:
    deps = read_json(REPO / "ps5" / "toolchain" / "game-dependencies.json")
    out = REPO / ".tools" / "game-deps"
    out.mkdir(parents=True, exist_ok=True)
    for dep in deps:
        archive = out / dep["archive"]
        download(dep["url"], archive, dep["sha256"])
        if not (out / dep["directory"]).is_dir():
            print(f"+ tar -xf {dep['archive']} -C .tools/game-deps", flush=True)
            extract(archive, out)


def prepare_cxx_toolchain() -> None:
    lock = read_json(REPO / "ps5" / "toolchain" / "sources.json")
    release = lock["llvmRelease"]
    sources = REPO / ".tools" / "cxx-src"
    sysroot = REPO / ".tools" / "ps5-sysroot"
    sources.mkdir(parents=True, exist_ok=True)
    sysroot.mkdir(parents=True, exist_ok=True)
    for archive in lock["archives"]:
        path = sources / archive["name"]
        url = (f"https://github.com/llvm/llvm-project/releases/download/llvmorg-{release}/"
               f"{archive['name']}")
        download(url, path, archive["sha256"])
        directory = archive["name"].replace(".tar.xz", "")
        if not (sources / directory).is_dir():
            print(f"+ tar -xf {archive['name']} -C .tools/cxx-src", flush=True)
            extract(path, sources)
    # The helper modules are read from runtimes/cmake/Modules; copy them in
    # directly, as the PS1 does after fetching each one.
    for helper in lock["cmakeHelpers"]:
        url = (f"https://raw.githubusercontent.com/llvm/llvm-project/llvmorg-{release}/"
               f"runtimes/cmake/Modules/{helper['name']}")
        download(url, sources / "runtimes" / "cmake" / "Modules" / helper["name"],
                 helper["sha256"])
    # Prepare-CxxToolchain.ps1 also exposes the cmake source tree under the
    # plain name "cmake" (some LLVM cmake modules look for it there).
    if not (sources / "cmake").exists() and (sources / "cmake-18.1.8.src").is_dir():
        shutil.copytree(sources / "cmake-18.1.8.src", sources / "cmake")
    headers = lock["cHeaders"]
    base = sources / "freebsd-9.3-base.txz"
    download(headers["url"], base, headers["sha256"])
    ready = sysroot / "headers-ready.json"
    if not ready.exists():
        # Materialize the archive's symlinks as copies (their targets are also
        # in the archive) exactly as Prepare-CxxToolchain.ps1 does, so a host
        # that cannot or does not create links still gets a complete include
        # tree. Both resolved paths are validated before anything is written.
        print(f"+ tar -xf {base.name} ./usr/include -C .tools/ps5-sysroot", flush=True)
        extract(base, sysroot, members=["./usr/include"], materialize_symlinks=True)
        ready.write_text(
            '{\n "sourceHash": "%s",\n "nativeAbiVerified": false\n}\n' % headers["sha256"].upper(),
            encoding="utf-8")


def prepare_desktop_check() -> None:
    out = REPO / ".tools" / "desktop-check"
    out.mkdir(parents=True, exist_ok=True)
    packages = [
        {"file": "dawn-windows-amd64.tar.gz",
         "url": "https://github.com/theofficialgman/dawn-build/releases/download/"
                "v20260603.191052/dawn-windows-amd64.tar.gz",
         "hash": "13BE9CFF8B9B179C42DCD16AEABB6EFFCC8F0DFDCC14463EDA2A5CAEDA225142"},
        {"file": "abseil-20240722.0.tar.gz",
         "url": "https://github.com/abseil/abseil-cpp/archive/refs/tags/20240722.0.tar.gz",
         "hash": "F50E5AC311A81382DA7FA75B97310E4B9006474F9560AC46F54A9967F07D4AE3"},
    ]
    for pkg in packages:
        archive = out / pkg["file"]
        download(pkg["url"], archive, pkg["hash"])
    dawn = out / "dawn"
    dawn.mkdir(exist_ok=True)
    if not (dawn / "include").is_dir():
        extract(out / "dawn-windows-amd64.tar.gz", dawn, members=["./include"])
    if not (out / "abseil-cpp-20240722.0").is_dir():
        extract(out / "abseil-20240722.0.tar.gz", out)


STEPS = {
    "sources": apply_sources,
    "llvm-mingw": prepare_llvm_mingw,
    "game-deps": prepare_game_dependencies,
    "cxx-toolchain": prepare_cxx_toolchain,
    "desktop-check": prepare_desktop_check,
}


def main(argv: list[str]) -> int:
    targets = argv[1:] or list(STEPS)
    for name in targets:
        if name not in STEPS:
            die(f"unknown fetch step: {name} (choose from {', '.join(STEPS)})")
    for name in targets:
        print(f"==> fetch: {name}", flush=True)
        STEPS[name]()
    print("Sources prepared. This is not yet a validated PS5 standard library.",
          flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
