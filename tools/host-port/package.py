"""Assemble the PPSA99611 development package (Package-Game.ps1).

Copies the signed executable, libc.prx, shaders, DSP coefficient, Wii bootstrap
and title metadata into a package folder, generates the param/sysver metadata
with sharpprospero-bindgen, and refuses to overwrite an existing package.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import libc_stub  # noqa: E402
from common import REPO, die, file_sha256_upper, newest_dir, read_json, write_json  # noqa: E402

CONFIG_BASE = """[video]
graphics_api = "agc"
display_mode = "borderless"
resolution_multiplier = 1.0
frame_interpolation_fps = 0
texture_replacements = false
texture_dumps = false
disable_copy_filter = true

[network]
enabled = true

[discord]
enabled = false

[paths]
dvd_root = "/app0/DATA"
nand_root = "/app0/UserData/NAND"
"""


def main(argv: list[str]) -> int:
    title_id = "PPSA99611"
    title_name = "Mario Kart Wii - PS5 development PPSA99611"
    retro = False
    exe_arg = None
    pack_arg = None
    for arg in argv[1:]:
        if arg == "--retro-rewind":
            retro = True
        elif arg.startswith("--title-name="):
            title_name = arg.split("=", 1)[1]
        elif arg.startswith("--title-id="):
            title_id = arg.split("=", 1)[1]
        elif arg.startswith("--retro-pack="):
            pack_arg = Path(arg.split("=", 1)[1])
        elif arg.startswith("--executable="):
            exe_arg = Path(arg.split("=", 1)[1])

    exe = exe_arg or newest_dir(REPO / "artifacts" / "game-executable")
    if exe is None:
        die("no artifacts/game-executable/* build; link the game first")
    built = read_json(exe / "build.json")
    if not built.get("linkedGame"):
        die(f"{exe}/build.json is not a linked game")
    if file_sha256_upper(exe / "eboot.bin") != built["ebootSha256"]:
        die("game executable manifest mismatch (eboot.bin changed)")

    retro_pack = None
    if retro:
        # Retro Rewind product: embed the RetroRewind6 asset tree at
        # /app0/RetroRewind6 and point the runtime's Riivolution overlay at it.
        retro_pack = pack_arg or (REPO / "Wiicompiled" / "PulsarPacks" / "completed"
                                  / "RetroRewind" / "RetroRewind6")
        if not (retro_pack / "Binaries" / "Code.pul").exists():
            die(f"RetroRewind6 pack not found at {retro_pack} (expected Binaries/Code.pul)")

    out_root = REPO / "artifacts" / "game-package" / (
        time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() % 1 * 1000):03d}")
    package = out_root / title_id
    if package.exists():
        die(f"refusing to overwrite an existing package: {package}")

    for folder in ("sce_sys", "sce_module", "shaders", "runtime/assets/dsp", "UserData/NAND"):
        (package / folder).mkdir(parents=True, exist_ok=True)

    inputs = {
        "eboot.bin": exe / "eboot.bin",
        "sce_module/libc.prx": REPO / ".tools" / "console" / "libc.prx",
        "sce_sys/icon0.png": REPO / "SharpProspero" / "samples" / "prospero-3d" / "sce_sys" / "icon0.png",
        "shaders/gx_vertex.sb": REPO / "artifacts" / "gx-varyings" / "vertex.sb",
        "shaders/gx_pixel.sb": REPO / "artifacts" / "tev-fragment" / "tev_probe.sb",
        "shaders/copy_vertex.sb": REPO / "ps5link-sdk" / "shaders" / "third_party" / "sharpprospero" / "mesh_vs.sb",
        "shaders/copy_pixel.sb": REPO / "artifacts" / "gx-copy-shader" / "copy.sb",
        "runtime/assets/dsp/dsp_coef.bin": REPO / "Wiicompiled" / "runtime" / "assets" / "dsp" / "dsp_coef.bin",
    }
    for name, source in inputs.items():
        if not source.exists():
            die(f"missing package input {name}: {source}")
        shutil.copyfile(source, package / name)
    shutil.copytree(REPO / "Wiicompiled" / "runtime" / "assets" / "wii",
                    package / "wii_bootstrap", dirs_exist_ok=True)
    config_paths = 'dvd_root = "/app0/DATA"\nnand_root = "/app0/UserData/NAND"\n'
    if retro:
        # The generated mod manifest registers the RetroRewind6 overlay root
        # relative to /app0; declaring it explicitly keeps the root visible even
        # if that registration ordering changes.
        shutil.copytree(retro_pack, package / "RetroRewind6", dirs_exist_ok=True)
        config_paths += 'retro_rewind_root = "/app0/RetroRewind6"\n'
    config = CONFIG_BASE.replace(
        'dvd_root = "/app0/DATA"\nnand_root = "/app0/UserData/NAND"\n', config_paths)
    (package / "UserData" / "Config.toml").write_text(config, encoding="utf-8")

    param = read_json(REPO / "SharpProspero" / "samples" / "prospero-3d" / "sce_sys" / "param.json")
    param["titleId"] = title_id
    param["conceptId"] = title_id[4:]
    param["contentId"] = f"IV0000-{title_id}_00-WIICOMPILEDMKW01"
    param.setdefault("localizedParameters", {}).setdefault("en-US", {})["titleName"] = title_name
    write_json(package / "sce_sys" / "param.json", param)
    (package / "portable.txt").write_text(
        "WiiCompiled PS5 portable development package\n", encoding="utf-8")
    (package / "UserData" / "NAND" / "README.txt").write_text(
        "WiiCompiled stores this development title's Wii save data in this directory.\n",
        encoding="utf-8")
    (package / "UserData" / "Config.toml").write_text(CONFIG_BASE, encoding="utf-8")

    dotnet = libc_stub.dotnet()
    bindgen = libc_stub.bindgen()
    if subprocess.run([dotnet, "--roll-forward", "LatestMajor", str(bindgen),
                       "param", "--folder", str(package)], cwd=str(REPO)).returncode != 0:
        die("game parameter validation failed")
    if subprocess.run([dotnet, "--roll-forward", "LatestMajor", str(bindgen),
                       "sysver", "--folder", str(package), "--policy", "match", "--apply"],
                      cwd=str(REPO)).returncode != 0:
        die("game firmware declaration failed")

    files = {}
    for file in sorted(package.rglob("*")):
        if file.is_file():
            files[file.relative_to(package).as_posix()] = file_sha256_upper(file)
    write_json(out_root / "build.json", {
        "kind": "Mario Kart Wii development package",
        "titleId": title_id, "completePackage": True, "gamePlayable": False,
        "linkedGame": True, "hardwareVerified": False, "auroraBootstrap": True,
        "requiredGameData": f"/data/{title_id}/DATA", "runtimeGameData": "/app0/DATA",
        "executableBuild": str(exe), "ebootSha256": built["ebootSha256"],
        "files": files,
    })
    print(f"package: {package}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
