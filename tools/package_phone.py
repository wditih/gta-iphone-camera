"""Package this fork, keeping game files and third party apps out of the zip.
SPDX-License-Identifier: GPL-3.0-only
"""
import argparse
import hashlib
import importlib.metadata
import json
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--build", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    build = args.build.resolve()
    binary = build / "BIN/Release/RockstarEditorPlus.asi"
    bridge = build / "bridge/PhoneCameraBridge.exe"
    for required in (binary,bridge,ROOT/"PHONE_CAMERA_RU.md",ROOT/"LICENSE"):
        if not required.is_file():
            raise SystemExit(f"Missing deliverable: {required}")
    sha = subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip()
    source = args.output / "GTA-iPhone-Camera-source.zip"
    subprocess.run(["git","archive","--format=zip",f"--output={source.resolve()}","HEAD"],cwd=ROOT,check=True)
    main_ini = (build/"ini/RockstarEditorPlus.ini").read_text(encoding="utf8")
    # Keep the camera hook and streaming focus, remove unrelated first-run tuning.
    replacements = {"ReplayBlocks=128":"ReplayBlocks=3", "FastPrecache=1":"FastPrecache=0",
                    "FastPrecacheAudio=1":"FastPrecacheAudio=0", "PrecacheMaxMs=1500":"PrecacheMaxMs=0",
                    "UrgentModelLoadMs=250":"UrgentModelLoadMs=5000", "BypassProfanityFilter=1":"BypassProfanityFilter=0"}
    for old,new in replacements.items():
        main_ini = main_ini.replace(old,new)
    manifest = dict(version="0.1.0",commit=sha,variant="experimental Legacy Story Mode",game_tested=False,
                    iphone_tested=False,stock_export_tested=False,
                    upstream="https://github.com/crxhvrd/REPlus",license="GPL-3.0-only",
                    app_protocol="BlendMotion addon v3.3.2 BM51/BM59; app compatibility unverified",
                    files={"RockstarEditorPlus.asi":hashlib.sha256(binary.read_bytes()).hexdigest(),
                           "PhoneCameraBridge.exe":hashlib.sha256(bridge.read_bytes()).hexdigest()})
    with zipfile.ZipFile(args.output/"GTA-iPhone-Camera-prototype.zip","w",zipfile.ZIP_DEFLATED) as out:
        out.write(binary,"GameFiles/RockstarEditorPlus.asi")
        out.writestr("GameFiles/RockstarEditorPlus/RockstarEditorPlus.ini",main_ini)
        out.write(build/"ini/Render.ini","GameFiles/RockstarEditorPlus/Render.ini")
        out.writestr("GameFiles/RockstarEditorPlus/PhoneCamera.ini","[PhoneCamera]\nEnabled=1\nCollision=1\n")
        out.write(bridge,"PC/PhoneCameraBridge.exe")
        out.write(ROOT/"PHONE_CAMERA_RU.md","START_HERE_RU.md")
        out.write(ROOT/"LICENSE","LICENSE")
        out.write(ROOT/"src/vendor/minhook/LICENSE.txt","ThirdParty/MinHook-LICENSE.txt")
        for name in ("mss","Pillow","websockets","pyinstaller","altgraph"):
            distribution=importlib.metadata.distribution(name)
            for item in distribution.files or ():
                if "license" in str(item).lower() or "copying" in str(item).lower():
                    path=Path(distribution.locate_file(item))
                    if path.is_file():
                        out.write(path,"ThirdParty/"+name+"/"+path.name)
        out.write(source,"GTA-iPhone-Camera-source.zip")
        out.writestr("BUILD.json",json.dumps(manifest,indent=2)+"\n")
    for item in sorted(args.output.glob("*.zip")):
        print(item.name, item.stat().st_size, hashlib.sha256(item.read_bytes()).hexdigest())

if __name__ == "__main__":
    main()
