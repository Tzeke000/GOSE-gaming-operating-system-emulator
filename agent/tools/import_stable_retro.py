#!/usr/bin/env python3
"""Import RAM maps from stable-retro / gym-retro into GOSE game profiles.

stable-retro (Farama Foundation, the maintained gym-retro fork) ships community
RAM maps for hundreds of games as `data.json`:

    { "info": { "score": { "address": 128, "type": ">u4" },
                "lives": { "address": 1234, "type": "|u1" } } }

This converts one game's `data.json` into a GOSE profile (see
../gose_agent/profiles/README.md). We reuse their maps instead of hand-authoring
addresses — that's the hard, error-prone part already done and verified.

Note on addressing: stable-retro addresses are offsets into the core's RAM array,
which line up with RetroArch's achievement/`READ_CORE_RAM` space — so imported
profiles default to read_method="core_ram".

Usage:
    python3 tools/import_stable_retro.py <stable-retro-game-dir> [-o profiles/<name>.json]
    # e.g. .../site-packages/retro/data/stable/Airstriker-Genesis/

Find your install dir with:  python3 -c "import retro,os;print(os.path.join(os.path.dirname(retro.__file__),'data','stable'))"
"""
from __future__ import annotations

import argparse
import json
import os
import sys


def convert(game_dir: str) -> dict:
    data_path = os.path.join(game_dir, "data.json")
    if not os.path.isfile(data_path):
        raise SystemExit(f"no data.json in {game_dir}")
    with open(data_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    info = data.get("info", {})
    if not info:
        raise SystemExit(f"{data_path} has no 'info' section")

    game_name = os.path.basename(os.path.normpath(game_dir))
    system = ""
    meta_path = os.path.join(game_dir, "metadata.json")
    if os.path.isfile(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as fh:
                system = json.load(fh).get("system", "") or ""
        except (json.JSONDecodeError, OSError):
            pass

    fields = []
    for name, spec in info.items():
        if "address" not in spec or "type" not in spec:
            continue
        fields.append({
            "name": name,
            "address": int(spec["address"]),   # decimal RAM-array offset
            "type": spec["type"],               # stable-retro descriptor, e.g. ">u4"
        })
    fields.sort(key=lambda f: f["name"])

    return {
        "name": game_name.replace("-", " "),
        "system": system,
        "core": "",
        "read_method": "core_ram",
        "endian": "little",   # per-field descriptors carry their own endianness
        "match": {"game_substr": game_name.split("-")[0].lower(), "crc": ""},
        "notes": f"Imported from stable-retro '{game_name}'. Addresses are RAM "
                 f"offsets (read via READ_CORE_RAM). Verify the core exposes RAM "
                 f"to the Network Command Interface.",
        "fields": fields,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("game_dir", help="stable-retro game directory (contains data.json)")
    ap.add_argument("-o", "--out", help="output profile path (default: stdout)")
    a = ap.parse_args(argv)
    profile = convert(a.game_dir)
    text = json.dumps(profile, indent=2)
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print(f"wrote {a.out} ({len(profile['fields'])} fields)", file=sys.stderr)
    else:
        print(text)


if __name__ == "__main__":
    main()
