"""Unity-serialized Cubism models (as `extract` dumps them) -> standard model3.json folders.

A model folder `live2d_mdl_<char>-...` holds the CubismMoc (`_bytes` = the .moc3), the physics
controller (`_rig`), the texture PNG and one tiny JSON per drawable/part/parameter component (the
.moc3 already has those, so they're skipped). Expressions live in `live2d_exp_<name>_<char>_<variant>`
folders, shared by every model of that character.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from logging import getLogger
from pathlib import Path

logger = getLogger("live2d")

COMPONENT = {0: "X", 1: "Y", 2: "Angle"}  # CubismPhysicsSourceComponent
BLEND = {0: "Overwrite", 1: "Add", 2: "Multiply"}  # CubismParameterBlendMode


def load(p: Path):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def physics3(rig: dict) -> dict:
    settings, dictionary = [], []
    n_in = n_out = n_vert = 0
    for i, sub in enumerate(rig["SubRigs"], 1):
        sid = f"PhysicsSetting{i}"
        dictionary.append({"Id": sid, "Name": sub.get("Name", "")})
        inputs = [{
            "Source": {"Target": "Parameter", "Id": x["SourceId"]},
            "Weight": x["Weight"],
            "Type": COMPONENT[x["SourceComponent"]],
            "Reflect": bool(x["IsInverted"]),
        } for x in sub["Input"]]
        outputs = []
        for x in sub["Output"]:
            comp = x["SourceComponent"]
            ts = x["TranslationScale"]
            scale = x["AngleScale"] if comp == 2 else (ts["x"] if comp == 0 else ts["y"])
            if not scale:
                scale = x["AngleScale"] or ts["x"] or ts["y"]
            outputs.append({
                "Destination": {"Target": "Parameter", "Id": x["DestinationId"]},
                "VertexIndex": x["ParticleIndex"],
                "Scale": scale,
                "Weight": x["Weight"],
                "Type": COMPONENT[comp],
                "Reflect": bool(x["IsInverted"]),
            })
        verts = [{
            "Position": {"X": p["InitialPosition"]["x"], "Y": p["InitialPosition"]["y"]},
            "Mobility": p["Mobility"], "Delay": p["Delay"],
            "Acceleration": p["Acceleration"], "Radius": p["Radius"],
        } for p in sub["Particles"]]
        norm = {k: {"Minimum": v["Minimum"], "Default": v["Default"], "Maximum": v["Maximum"]}
                for k, v in sub["Normalization"].items()}
        settings.append({"Id": sid, "Input": inputs, "Output": outputs,
                         "Vertices": verts, "Normalization": norm})
        n_in += len(inputs)
        n_out += len(outputs)
        n_vert += len(verts)
    g, w = rig.get("Gravity", {"x": 0, "y": -1}), rig.get("Wind", {"x": 0, "y": 0})
    meta = {
        "PhysicsSettingCount": len(settings), "TotalInputCount": n_in,
        "TotalOutputCount": n_out, "VertexCount": n_vert,
        "EffectiveForces": {"Gravity": {"X": g["x"], "Y": g["y"]}, "Wind": {"X": w["x"], "Y": w["y"]}},
        "PhysicsDictionary": dictionary,
    }
    if rig.get("Fps"):
        meta["Fps"] = rig["Fps"]
    return {"Version": 3, "Meta": meta, "PhysicsSettings": settings}


def exp3(e: dict) -> dict:
    return {
        "Type": "Live2D Expression",
        "FadeInTime": e.get("FadeInTime", 0.5),
        "FadeOutTime": e.get("FadeOutTime", 0.5),
        "Parameters": [{"Id": p["Id"], "Value": p["Value"], "Blend": BLEND[p["Blend"]]}
                       for p in e["Parameters"]],
    }


def expressions_by_char(root: Path) -> dict[str, list[tuple[str, dict]]]:
    """{char_id: [(name, data)]} from live2d_exp_<name>_<char>_<variant> folders."""
    out: dict[str, list[tuple[str, dict]]] = {}
    for d in sorted(root.glob("live2d_exp_*_*_*")):
        m = re.fullmatch(r"live2d_exp_(.+)_(\d+)_(\d+)", d.name)
        if not m or not d.is_dir():
            continue
        name, char, variant = m.groups()
        for f in d.glob("*.json"):
            data = load(f)
            if "Parameters" in data:
                label = name if variant == "000" else f"{name}_{variant}"
                out.setdefault(char, []).append((label, data))
    return out


def convert(model_dir: Path, out_root: Path, exps: dict) -> None:
    char = re.fullmatch(r"live2d_mdl_(\d+)-.+", model_dir.name).group(1)
    moc = rig = None
    for f in model_dir.glob("*.json"):
        if f.stat().st_size < 4096:
            continue  # per-drawable/part/parameter components; the moc3 already has them
        data = load(f)
        b = data.get("_bytes")
        if b and bytes(b[:4]) == b"MOC3":
            moc = bytes(b)
        elif "_rig" in data:
            rig = data["_rig"]
    if moc is None:
        logger.warning("skip %s: no MOC3", model_dir.name)
        return

    out = out_root / model_dir.name
    out.mkdir(parents=True, exist_ok=True)
    (out / "model.moc3").write_bytes(moc)

    tex_dir = out / "textures"
    tex_dir.mkdir(exist_ok=True)
    textures = []
    for i, png in enumerate(sorted(model_dir.glob("*.png"))):
        shutil.copyfile(png, tex_dir / f"texture_{i:02d}.png")
        textures.append(f"textures/texture_{i:02d}.png")

    refs: dict = {"Moc": "model.moc3", "Textures": textures}
    if rig:
        (out / "model.physics3.json").write_text(
            json.dumps(physics3(rig), ensure_ascii=False, indent=1), encoding="utf-8")
        refs["Physics"] = "model.physics3.json"

    exp_list = []
    if exps.get(char):
        (out / "expressions").mkdir(exist_ok=True)
        for label, data in exps[char]:
            rel = f"expressions/{label}.exp3.json"
            (out / rel).write_text(json.dumps(exp3(data), indent=1), encoding="utf-8")
            exp_list.append({"Name": label, "File": rel})
        refs["Expressions"] = exp_list

    groups = []
    eyes = [p for p in ("ParamEyeLOpen", "ParamEyeROpen") if p.encode() in moc]
    if eyes:
        groups.append({"Target": "Parameter", "Name": "EyeBlink", "Ids": eyes})
    if b"ParamMouthOpenY" in moc:
        groups.append({"Target": "Parameter", "Name": "LipSync", "Ids": ["ParamMouthOpenY"]})

    model3 = {"Version": 3, "FileReferences": refs, "Groups": groups}
    # Named after the model, not "model.model3.json": viewers may key per-model settings by file name.
    name = model_dir.name.removeprefix("live2d_mdl_")
    (out / f"{name}.model3.json").write_text(json.dumps(model3, indent=1), encoding="utf-8")
    logger.info("%s: moc3 v%d, %d tex, physics=%s, %d exp",
                model_dir.name, moc[4], len(textures), bool(rig), len(exp_list))


def main(args: argparse.Namespace) -> int:
    indir, outdir = Path(args.indir), Path(args.outdir)
    assert indir.resolve() != outdir.resolve(), "input and output must differ"
    exps = expressions_by_char(indir)
    for d in sorted(indir.glob("live2d_mdl_*")):
        if d.is_dir():
            convert(d, outdir, exps)
    return 0
