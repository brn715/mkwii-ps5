#!/usr/bin/env bash
# =============================================================================
# tools/build-macos.sh - reproducible macOS arm64 build of Mario Kart Wii (PS5)
# =============================================================================
#
# This is the macOS/Linux counterpart of the Windows PowerShell pipeline under
# ps5/. It runs the entire build from a clean checkout: fetch + verify sources,
# build libc++, translate and compile the game, compile the runtime, build SDL
# input/audio, build the AGC shaders, link and sign the executable, and
# assemble the PPSA99611 development package. The .ps1 scripts are untouched and
# still describe the Windows path; this script performs the same ordered work
# with the same safety checks (checksum verification, refusal to overwrite,
# audit reports).
#
# USAGE
#   tools/build-macos.sh --check        verify every prerequisite and print the plan
#   tools/build-macos.sh                 run the whole build (fetch, build, link, package)
#   tools/build-macos.sh fetch           only fetch + verify sources
#   tools/build-macos.sh build           only build/translate/compile
#   tools/build-macos.sh link            only link, sign and package
#   tools/build-macos.sh package         only assemble the PPSA99611 package
#   tools/build-macos.sh clean-plan      print what a clean would remove (no action)
#
#   Options:
#     --jobs N          parallel compile jobs (default: CPU count, min 4)
#     --threads N       translator threads (default: 16)
#     --retro-rewind    also translate the Retro Rewind mod profile (see note)
#
# The --check mode never builds anything; it exits non-zero and lists every
# missing prerequisite. The script is idempotent: fetched archives are
# re-verified rather than re-downloaded, cmake/ninja builds are incremental, and
# every linked executable and package lands in a fresh timestamped folder, so a
# re-run never clobbers an earlier output (the "refuse to overwrite" checks in
# the .ps1s apply to the fixed-name intermediates, which are regenerated).
#
# PINNED VERSIONS (must match sources.lock.json / ps5/toolchain/)
#   - .NET SDK 10.x on PATH (`dotnet`); the translator targets net8.0 and is
#     launched with `dotnet --roll-forward LatestMajor`.
#   - cmake + ninja (Homebrew or any recent release) and python3.
#   - LLVM 18.1.8 shader tools at /opt/homebrew/opt/llvm@18/bin (llvm-mc,
#     llvm-objcopy, clang, llc). Override with MKW_LLVM18_BIN.
#   - llvm-mingw 20260908-ucrt-macos-universal under .tools/ (fetched here).
#   - A signed, user-supplied libc.prx at .tools/console/libc.prx.
#   - Mario Kart Wii PAL (RMCP01) main.dol + StaticR.rel at Wiicompiled/Assets/.
#   - artifacts/kernel-exports/result.json captured from the console.
#
# Nothing here installs or modifies Sony software; libc.prx and the game data
# are supplied locally and remain untracked.
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY=python3
HOST_PORT="$ROOT/tools/host-port"
ARTIFACTS="$ROOT/artifacts"

JOBS="$( (sysctl -n hw.ncpu 2>/dev/null || nproc 2>/dev/null || echo 4) )"
if [ "$JOBS" -lt 4 ]; then JOBS=4; fi
THREADS=16
RETRO=0
STAGES=()

die() { echo "error: $*" >&2; exit 1; }
info() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

while [ $# -gt 0 ]; do
    case "$1" in
        --check)      exec "$PY" "$HOST_PORT/check.py" ;;
        --jobs)       JOBS="$2"; shift 2 ;;
        --threads)    THREADS="$2"; shift 2 ;;
        --retro-rewind) RETRO=1; shift ;;
        -h|--help)    sed -n '2,48p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        fetch|build|link|package|clean-plan) STAGES+=("$1"); shift ;;
        *) die "unknown argument: $1 (see --help)";;
    esac
done

if [ ${#STAGES[@]} -eq 0 ]; then STAGES=(fetch build link package); fi

CMAKE=cmake
NINJA=ninja
export MKW_LLVM18_BIN="${MKW_LLVM18_BIN:-/opt/homebrew/opt/llvm@18/bin}"

require_host_tools() {
    command -v "$PY"    >/dev/null || die "python3 not found on PATH"
    command -v cmake    >/dev/null || die "cmake not found on PATH"
    command -v ninja    >/dev/null || die "ninja not found on PATH"
    command -v git      >/dev/null || die "git not found on PATH"
    command -v dotnet   >/dev/null || die "dotnet not found on PATH (need the .NET 10 SDK)"
    [ -x "$MKW_LLVM18_BIN/llvm-mc" ] || die "LLVM 18.1.8 tools not found at $MKW_LLVM18_BIN (set MKW_LLVM18_BIN)"
    "$MKW_LLVM18_BIN/llvm-mc" --version | grep -q '18\.1\.8' \
        || die "$MKW_LLVM18_BIN/llvm-mc is not LLVM 18.1.8"
}

# ---------------------------------------------------------------------------
# fetch
# ---------------------------------------------------------------------------
stage_fetch() {
    info "Fetching and verifying sources"
    "$PY" "$HOST_PORT/fetch_deps.py"
    info "Verifying game assets"
    MKW_REPO_ROOT="$ROOT" "$PY" - <<'PY'
import os, sys
from pathlib import Path
sys.path.insert(0, str(Path(os.environ["MKW_REPO_ROOT"]) / "tools" / "host-port"))
import check
c = check.Checker()
check.check_game_assets(c)
check.check_dependencies(c)
for e in c.errors:
    print(f"error: {e}", file=sys.stderr)
sys.exit(1 if c.errors else 0)
PY
}

# ---------------------------------------------------------------------------
# libc++ (Build-Libcxx.ps1)
# ---------------------------------------------------------------------------
stage_libcxx() {
    info "Building libc++ for x86_64-unknown-freebsd9.3"
    [ -d "$ROOT/.tools/cxx-src/libcxx-18.1.8.src" ] \
        || die "LLVM sources missing; run: tools/build-macos.sh fetch"
    local build="$ARTIFACTS/libcxx-build"
    "$CMAKE" -S "$ROOT/ps5/toolchain/libcxx" -B "$build" -G Ninja \
        -DCMAKE_MAKE_PROGRAM="$(command -v "$NINJA")" \
        -DCMAKE_TOOLCHAIN_FILE="$ROOT/ps5/cmake/ps5-cxx.cmake" \
        -DCMAKE_BUILD_TYPE=Release > "$ARTIFACTS/libcxx-configure.log" 2>&1 \
        || die "libc++ configure failed; see artifacts/libcxx-configure.log"
    "$CMAKE" --build "$build" --target cxx_static -j "$JOBS" \
        > "$ARTIFACTS/libcxx-build.log" 2>&1 \
        || die "libc++ build failed; see artifacts/libcxx-build.log"
    "$PY" - "$build" <<'PY'
import json, sys, hashlib
from pathlib import Path
build = Path(sys.argv[1])
lib = build / "lib" / "libc++.a"
sha = hashlib.sha256(lib.read_bytes()).hexdigest().upper()
(build / "build-result.json").write_text(json.dumps({
    "archiveSha256": sha, "target": "x86_64-unknown-freebsd9.3",
    "nativeTested": False}, indent=2) + "\n")
PY
}

# ---------------------------------------------------------------------------
# .NET tools (translator, game_link, posix_stub, bindgen)
# ---------------------------------------------------------------------------
stage_dotnet_tools() {
    info "Building the .NET tools (Release)"
    dotnet build "$ROOT/Wiicompiled/translator/src/Translator.Cli/Translator.Cli.csproj" \
        -c Release > "$ARTIFACTS/translator-build.log" 2>&1 \
        || die "Translator.Cli build failed; see artifacts/translator-build.log"
    dotnet build "$ROOT/ps5/tools/game_link/game_link.csproj" \
        -c Release > "$ARTIFACTS/game-link-build.log" 2>&1 \
        || die "game_link build failed; see artifacts/game-link-build.log"
    dotnet build "$ROOT/ps5/tools/posix_stub/posix_stub.csproj" \
        -c Release > "$ARTIFACTS/posix-stub-build.log" 2>&1 \
        || die "posix_stub build failed; see artifacts/posix-stub-build.log"
}

# ---------------------------------------------------------------------------
# translate (Translate-Game.ps1)
# ---------------------------------------------------------------------------
stage_translate() {
    info "Translating the base game"
    local cli="$ROOT/Wiicompiled/translator/src/Translator.Cli/bin/Release/net8.0/Translator.Cli.dll"
    [ -f "$cli" ] || die "Translator.Cli not built; run: tools/build-macos.sh build"
    local project="projects/mkwii/recomp.yml"
    (
        cd "$ROOT/Wiicompiled"
        dotnet --roll-forward LatestMajor "$cli" translate-recursive 0x800060A4 \
            --project "$project" --threads "$THREADS" \
            --output-metadata generated/base_translation_output.json \
            || die "translation failed"
        dotnet --roll-forward LatestMajor "$cli" generate-data-init --project "$project" \
            || die "generate-data-init failed"
        dotnet --roll-forward LatestMajor "$cli" emit-base-manifest --project "$project" \
            || die "emit-base-manifest failed"
        if [ "$RETRO" -eq 1 ]; then
            local modOut="build/mods/retro_rewind_full_cpp"
            dotnet --roll-forward LatestMajor "$cli" translate-mod --project "$project" \
                --profile retro-rewind --base-manifest build/base/mkwii_base_manifest.json \
                --skip-retro-wfc --emit-cpp --threads "$THREADS" \
                || die "translate-mod failed"
            dotnet --roll-forward LatestMajor "$cli" emit-build-shards --project "$project" \
                --resolved-profile "$modOut/resolved_dispatch_profile.json" \
                --retro-cpp-dir "$modOut/cpp" \
                || die "emit-build-shards (retro) failed"
        else
            dotnet --roll-forward LatestMajor "$cli" emit-build-shards --project "$project" \
                || die "emit-build-shards failed"
        fi
    )
}

# ---------------------------------------------------------------------------
# compile game (Compile-Game.ps1)
# ---------------------------------------------------------------------------
stage_compile_game() {
    info "Compiling the translated game"
    local suffix="" build="$ARTIFACTS/game-build"
    local retro_flag=OFF
    if [ "$RETRO" -eq 1 ]; then suffix="-retro"; retro_flag=ON; build="$ARTIFACTS/game-build-retro"; fi
    "$CMAKE" -S "$ROOT/ps5" -B "$build" -G Ninja \
        -DCMAKE_MAKE_PROGRAM="$(command -v "$NINJA")" \
        -DCMAKE_TOOLCHAIN_FILE="$ROOT/ps5/cmake/ps5-cxx.cmake" \
        -DCMAKE_BUILD_TYPE=Release \
        -DMKW_PS5_BUILD_CPP_ADAPTER=ON \
        -DMKW_PS5_BUILD_GAME_OBJECTS=ON \
        -DMKW_PS5_RETRO_REWIND="$retro_flag" \
        -DMKW_PS5_FUNCTION_PROFILE=OFF > "$ARTIFACTS/game-configure$suffix.log" 2>&1 \
        || die "game configure failed; see artifacts/game-configure$suffix.log"
    "$CMAKE" --build "$build" -j "$JOBS" > "$ARTIFACTS/game-build$suffix.log" 2>&1 \
        || die "game object compilation failed; see artifacts/game-build$suffix.log"
    "$PY" - "$build" <<'PY'
import json, sys, hashlib
from pathlib import Path
build = Path(sys.argv[1])
arch = build / "libmkw_ps5_translated.a"
(build / "compile-result.json").write_text(json.dumps({
    "translatedArchiveSha256": hashlib.sha256(arch.read_bytes()).hexdigest().upper(),
    "compiled": True, "linkedGame": False, "nativeExecution": False}, indent=2) + "\n")
PY
}

# ---------------------------------------------------------------------------
# compile runtime (Compile-Runtime.ps1)
# ---------------------------------------------------------------------------
stage_compile_runtime() {
    info "Compiling the native runtime"
    local build="$ARTIFACTS/runtime-build"
    mkdir -p "$build"
    printf '{\n "compiled": false,\n "linkedGame": false,\n "nativeExecution": false\n}\n' \
        > "$build/compile-result.json"
    "$CMAKE" -S "$ROOT/ps5" -B "$build" -G Ninja \
        -DCMAKE_MAKE_PROGRAM="$(command -v "$NINJA")" \
        -DCMAKE_TOOLCHAIN_FILE="$ROOT/ps5/cmake/ps5-cxx.cmake" \
        -DCMAKE_BUILD_TYPE=Release \
        -DMKW_PS5_BUILD_CPP_ADAPTER=ON \
        -DMKW_PS5_BUILD_RUNTIME_OBJECTS=ON > "$ARTIFACTS/runtime-configure.log" 2>&1 \
        || die "runtime configure failed; see artifacts/runtime-configure.log"
    local targets=(mkw_ps5_runtime mkw_ps5_runtime_dependencies mkw_ps5_texture_decode
        mkw_ps5_agc_texture mkw_ps5_gx_frontend mkw_ps5_gx_registers mkw_ps5_gx_textures
        mkw_ps5_gx_tev mkw_ps5_gx_material mkw_ps5_gx_geometry mkw_ps5_gx_viewport
        mkw_ps5_gx_copy_commands mkw_ps5_native_input mkw_ps5_virtual_pad mkw_ps5_aurora_input)
    "$CMAKE" --build "$build" --target "${targets[@]}" -j "$JOBS" -- -k 0 \
        > "$ARTIFACTS/runtime-build.log" 2>&1 \
        || die "runtime compilation failed; see artifacts/runtime-build.log"
    "$PY" - "$build" <<'PY'
import json, sys
from pathlib import Path
build = Path(sys.argv[1])
graph = json.loads((build / "runtime-graph.json").read_text())
objects = list((build / "CMakeFiles" / "mkw_ps5_runtime.dir").rglob("*.o"))
if len(objects) != graph["translationUnits"]:
    sys.exit(f"compiled runtime object count {len(objects)} differs from the graph "
             f"{graph['translationUnits']}")
gx = list((build / "CMakeFiles" / "mkw_ps5_gx_frontend.dir").rglob("*.o"))
if len(gx) != 19:
    sys.exit(f"expected 19 GX frontend units, got {len(gx)}")
regs = list((build / "CMakeFiles" / "mkw_ps5_gx_registers.dir").rglob("*.o"))
if len(regs) != 3:
    sys.exit(f"expected 3 GX register units, got {len(regs)}")
(build / "compile-result.json").write_text(json.dumps({
    "compiled": True, "translationUnits": len(objects),
    "gxFrontendTranslationUnits": len(gx), "gxRegisterTranslationUnits": len(regs),
    "linkedGame": False, "nativeExecution": False}, indent=2) + "\n")
PY
}

# ---------------------------------------------------------------------------
# SDL input + audio (Build-SdlInput.ps1 / Build-SdlAudio.ps1)
# ---------------------------------------------------------------------------
stage_sdl() {
    info "Building SDL3 input and audio archives"
    for variant in input audio; do
        local out="$ARTIFACTS/sdl-$variant"
        mkdir -p "$out"
        local native=OFF
        if [ "$variant" = audio ]; then native=ON; fi
        "$CMAKE" -S "$ROOT/ps5/sdl" -B "$out" -G Ninja \
            -DCMAKE_MAKE_PROGRAM="$(command -v "$NINJA")" \
            -DCMAKE_TOOLCHAIN_FILE="$ROOT/ps5/cmake/ps5-cxx.cmake" \
            -DCMAKE_BUILD_TYPE=Release -DMKW_PS5_NATIVE_AUDIO="$native" \
            > "$out/configure.log" 2>&1 || die "SDL $variant configure failed"
        "$CMAKE" --build "$out" --target SDL3-static -j "$JOBS" \
            > "$out/build.log" 2>&1 || die "SDL $variant build failed"
        "$PY" - "$out" "$variant" <<'PY'
import json, sys, hashlib
from pathlib import Path
out = Path(sys.argv[1]); variant = sys.argv[2]
arch = out / "sdl" / "libSDL3.a"
report = {"compiled": True, "nativeExecution": False, "archive": str(arch),
          "sha256": hashlib.sha256(arch.read_bytes()).hexdigest().upper(),
          "video": False, "filesystemOperations": True, "virtualJoystick": True}
if variant == "audio":
    report.update({"audio": True, "driver": "ps5", "outputRate": 48000,
                   "outputChannels": 2, "format": "S16LE", "grain": 1024})
else:
    report.update({"audio": False, "platformPathDiscovery": False})
(out / "result.json").write_text(json.dumps(report, indent=2) + "\n")
PY
    done
}

# ---------------------------------------------------------------------------
# shaders (Build-Shaders.ps1 + ps5/tools/build_*_shader.py)
# ---------------------------------------------------------------------------
stage_shaders() {
    info "Building AGC shaders"
    local py="$ROOT/ps5/tools"
    "$PY" "$py/build_vertex_shader.py" --varyings \
        || die "vertex shader build failed"
    "$PY" "$py/build_tev_shader.py" --fragment-output \
        || die "TEV fragment shader build failed"
    "$PY" "$py/build_copy_shader.py" \
        || die "copy shader build failed"
    for f in artifacts/gx-varyings/vertex.sb artifacts/tev-fragment/tev_probe.sb \
             artifacts/gx-copy-shader/copy.sb; do
        [ -f "$ROOT/$f" ] || die "shader output missing: $f"
    done
}

# ---------------------------------------------------------------------------
# link + sign + package (Inspect-GameLink + Build-GameExecutable + Package-Game)
# ---------------------------------------------------------------------------
stage_link() {
    info "Linking, signing and packaging"
    local game="$ARTIFACTS/game-build"
    if [ "$RETRO" -eq 1 ]; then game="$ARTIFACTS/game-build-retro"; fi
    "$PY" "$HOST_PORT/build_executable.py" "$game"
    # package.py defaults to the newest artifacts/game-executable/* build.
    "$PY" "$HOST_PORT/package.py"
}

stage_package() {
    info "Assembling the PPSA99611 package"
    "$PY" "$HOST_PORT/package.py"
}

stage_clean_plan() {
    cat <<'EOF'
A clean removes only generated outputs; nothing tracked and nothing you supply:
  rm -rf artifacts/
  rm -rf Wiicompiled/generated/ Wiicompiled/build/
  rm -rf .tools/game-deps/ .tools/cxx-src/ .tools/ps5-sysroot/ .tools/desktop-check/
  rm -f  .tools/llvm-mingw-*.tar.xz
Keep: .tools/console/libc.prx, Wiicompiled/Assets/{main.dol,StaticR.rel},
      artifacts/kernel-exports/result.json, the cloned dependency repos.
(ps5/Clean-Artifacts.ps1 only trims regenerable per-run folders; run it on Windows.)
EOF
}

main() {
    require_host_tools
    mkdir -p "$ARTIFACTS"
    for stage in "${STAGES[@]}"; do
        case "$stage" in
            fetch)   stage_fetch ;;
            build)   stage_libcxx; stage_dotnet_tools; stage_translate;
                     stage_compile_game; stage_compile_runtime; stage_sdl; stage_shaders ;;
            link)    stage_link ;;
            package) stage_package ;;
            clean-plan) stage_clean_plan ;;
        esac
    done
    info "Done"
}

main
