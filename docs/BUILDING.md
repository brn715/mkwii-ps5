# Source code and build status

The PS5 port sources are in `ps5/`. Upstream dependencies are pinned in `sources.lock.json`; complete tracked modifications are in `patches/` and new dependency files are in `dependency-additions/`.

From a fresh checkout, use PowerShell 7:

```powershell
./tools/Get-Sources.ps1
```

This reconstructs the dependency source trees without bundling their entire history. Do **not** then run `ps5/Apply-SourcePatches.ps1`: its historical patch series overlaps the complete snapshot patches.

## Toolchain

The existing build scripts target Windows with PowerShell 7, Git, CMake/Ninja, Python, .NET 10 SDK plus the .NET 8 runtime, LLVM 18.1.8 for shaders, and llvm-mingw. Exact recorded versions are in `sources.lock.json` and `ps5/toolchain/`.

Scripts expect local tools beneath `.tools/` and default to a Visual Studio CMake/Ninja location. Supply the exposed `-CMake` and `-Ninja` arguments for another installation. A signed `libc.prx` supplied locally is expected at `.tools/console/libc.prx`.

## macOS / Linux (bash) pipeline

`tools/build-macos.sh` runs the same pipeline without PowerShell. It has been
verified on macOS arm64 (Apple silicon) and reuses the host-aware cmake
toolchain in `ps5/cmake/ps5-cxx.cmake`.

```bash
tools/build-macos.sh --check         # verify prerequisites and print the plan
tools/build-macos.sh                 # full build: fetch, build, link, package
tools/build-macos.sh fetch           # only fetch + verify sources
tools/build-macos.sh build           # libc++, translate + compile game/runtime, SDL, shaders
tools/build-macos.sh link            # audit, link, sign, assemble the PPSA99611 package
tools/build-macos.sh package         # package only
tools/build-macos.sh clean-plan      # print what a clean would remove (no action)
```

Options: `--jobs N` (compile parallelism), `--threads N` (translator threads),
`--force`, `--retro-rewind` (also translate the Retro Rewind mod profile).
`--check` never builds; it exits non-zero and lists every missing prerequisite.

Prerequisites on the host: the .NET 10 SDK (`dotnet` on PATH), cmake, ninja,
python3, git, LLVM 18.1.8 at `/opt/homebrew/opt/llvm@18/bin` (override with
`MKW_LLVM18_BIN`), and — supplied locally and untracked — `.tools/console/libc.prx`,
the PAL `main.dol`/`StaticR.rel`, and `artifacts/kernel-exports/result.json`.
The pinned llvm-mingw macOS-universal archive, the LLVM 18.1.8 sources, the
FreeBSD 9.3 headers and the runtime dependency archives are fetched and
checksum-verified by the script, matching the `ps5/Prepare-*.ps1` checks.

The .NET helpers live in `tools/host-port/` (dependency fetching with SHA-256
verification, the verified libc NID stub, the link-manifest assembly, the
executable/package builders, and `--check`). The Windows `.ps1` scripts are
unchanged.

## Build entry points

| Stage | Script |
| --- | --- |
| Prepare C++ sysroot | `ps5/Prepare-CxxToolchain.ps1` |
| Build libc++ | `ps5/Build-Libcxx.ps1` |
| Fetch runtime dependencies | `ps5/Prepare-GameDependencies.ps1` |
| Prepare shader tools | `ps5/Prepare-ShaderTools.ps1` |
| Extract and verify a personal PAL disc image | `ps5/Prepare-Game.ps1 -DiscImage <path>` |
| Translate game code after building Translator.Cli | `ps5/Translate-Game.ps1` |
| Compile translated code | `ps5/Compile-Game.ps1` |
| Compile native runtime | `ps5/Compile-Runtime.ps1` |
| Link and sign executable | `ps5/Build-GameExecutable.ps1` |
| Assemble local player package | `ps5/Build-ReleasePackage.ps1` |
| Verify downloadable ZIPs before publication | `python ps5/Verify-ReleaseArchives.py Kart-PS5.zip Backport.zip` |

These are existing entry points, **not a validated clean-build recipe**. The shader outputs, generated import stubs and supporting .NET tools also need preparation; the original development tree already contains those outputs. The local package builder includes the locally supplied runtime library, so its output must not be uploaded blindly.

Run the archive verifier on the **final ZIP files**, after excluding private game and Sony files. It checks the portable marker, required package files, SHA-256 manifest and firmware overlay metadata. This catches files dropped between package creation and publication.

The final supplied executable differs from the compared local builds. Its SHA-256 is recorded in `docs/RELEASE-NOTES.md`. Source-to-binary correspondence and a complete clean build remain to be established before publishing this as a reproducible release.

## Separate files and embedded data

The data initializer embeds original DOL/REL sections into the executable. The release retains the recompilation and its embedded code/data but excludes separate disc images, extracted files, generated data blobs, Sony libraries and personal saves. A binary without raw embedded game sections would require a separate runtime/build change, which has not been implemented.
