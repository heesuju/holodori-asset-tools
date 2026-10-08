"""Unity-serialized Cubism models (as `extract` dumps them) -> standard model3.json folders.

A model folder `live2d_mdl_<char>-...` holds the CubismMoc (`_bytes` = the .moc3), the physics
controller (`_rig`), the texture PNG and one tiny JSON per drawable/part/parameter component (the
.moc3 already has those, so they're skipped). Expressions live in `live2d_exp_<name>_<char>_<variant>`
folders, shared by every model of that character. Motions live in `live2d_mot_<name>` folders, shared
by every model: a Unity AnimationClip plus the game's MonoBehaviour for it (fade times).
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import struct
import zlib
from logging import getLogger
from pathlib import Path

logger = getLogger("live2d")

COMPONENT = {0: "X", 1: "Y", 2: "Angle"}  # CubismPhysicsSourceComponent
BLEND = {0: "Overwrite", 1: "Add", 2: "Multiply"}  # CubismParameterBlendMode
# A clip's curve targets are CRC32s of the GameObject path and the animated field.
TARGET_ATTRS = {zlib.crc32(b"Value"): ("Parameters/", "Parameter"), zlib.crc32(b"Opacity"): ("Parts/", "PartOpacity")}


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


def clip_curves(clip: dict) -> list[list[tuple]]:
    """Keys per curve, in binding order (streamed, dense, constant): (time, a, b, c, d), the value
    from that key to the next being a·dt³ + b·dt² + c·dt + d."""
    c = clip["m_MuscleClip"]["m_Clip"]
    c = c.get("data", c)
    streamed, dense = c["m_StreamedClip"], c["m_DenseClip"]
    keys: list[list[tuple]] = [[] for _ in range(streamed["curveCount"])]
    raw = struct.pack(f"<{len(streamed['data'])}I", *streamed["data"])
    pos = 0
    while pos < len(raw):
        t, n = struct.unpack_from("<fI", raw, pos)
        pos += 8
        for _ in range(n):
            idx, a, b, cc, d = struct.unpack_from("<i4f", raw, pos)
            pos += 20
            if math.isfinite(t) and t > -1e30:  # skip the -FLT_MAX start / +inf end frames
                keys[idx].append((t, a, b, cc, d))
    count, rate, frames = dense["m_CurveCount"], dense["m_SampleRate"], dense["m_FrameCount"]
    samples = dense["m_SampleArray"]
    for i in range(count):
        vals = [samples[f * count + i] for f in range(frames)]
        keys.append([(dense["m_BeginTime"] + f / rate, 0.0, 0.0,
                      ((vals[f + 1] - v) * rate if f + 1 < frames else 0.0), v) for f, v in enumerate(vals)])
    keys += [[(0.0, 0.0, 0.0, 0.0, v)] for v in c["m_ConstantClip"]["data"]]
    return keys


def segments(keys: list[tuple], duration: float) -> tuple[list[float], int, int]:
    """motion3 segments for one curve, its segment count and point count. Each key's cubic becomes an
    exact bezier (control points at thirds); non-finite slopes are steps."""
    r = lambda x: round(x, 4)
    t0, *_, v0 = keys[0]
    seg, n_seg, n_pts = [0.0, r(v0)], 0, 1
    if t0 > 0:
        seg += [0, r(t0), r(v0)]
        n_seg, n_pts = n_seg + 1, n_pts + 1
    for (t, a, b, c, d), nxt in zip(keys, keys[1:]):
        tn, vn, h = nxt[0], nxt[4], nxt[0] - t
        if not all(math.isfinite(x) for x in (a, b, c)):
            seg += [2, r(tn), r(vn)]
            n_pts += 1
        elif a == 0 and b == 0:
            seg += [0, r(tn), r(vn)]
            n_pts += 1
        else:
            end_slope = 3 * a * h * h + 2 * b * h + c
            seg += [1, r(t + h / 3), r(d + c * h / 3), r(t + 2 * h / 3), r(vn - end_slope * h / 3), r(tn), r(vn)]
            n_pts += 3
        n_seg += 1
    if keys[-1][0] < duration:
        seg += [0, r(duration), r(keys[-1][4])]
        n_seg, n_pts = n_seg + 1, n_pts + 1
    return seg, n_seg, n_pts


def load_motions(root: Path, ids: set[str]) -> list[dict]:
    """[{name, curves: [(target, id, segments, n_seg, n_pts)], duration, fps, loop, fade_in, fade_out}]."""
    targets = {zlib.crc32((prefix + i).encode()): (kind, i)
               for prefix, kind in TARGET_ATTRS.values() for i in ids}
    motions = []
    for d in sorted(root.glob("live2d_mot_*")):
        clip = info = None
        for f in d.glob("*.json"):
            data = load(f)
            if "m_ClipBindingConstant" in data:
                clip = data
            elif "baseAnimation" in data:
                info = data
        name = d.name.removeprefix("live2d_mot_")
        if clip is None:
            logger.warning("skip motion %s: no AnimationClip (re-run extract)", name)
            continue
        bindings = clip["m_ClipBindingConstant"]["genericBindings"]
        keys = clip_curves(clip)
        if len(keys) != len(bindings):
            logger.warning("skip motion %s: %d curves for %d bindings", name, len(keys), len(bindings))
            continue
        mc = clip["m_MuscleClip"]
        duration = mc["m_StopTime"] - mc["m_StartTime"]
        curves = []
        for b, k in zip(bindings, keys):
            target = targets.get(b["path"])
            if not k or b["attribute"] not in TARGET_ATTRS or not target or target[0] != TARGET_ATTRS[b["attribute"]][1]:
                continue
            curves.append((target[0], target[1], *segments(sorted(k), duration)))
        motions.append({
            "name": name, "curves": curves, "duration": duration, "fps": clip["m_SampleRate"],
            "loop": bool(mc["m_LoopTime"]) or name.endswith("_lp"),
            "fade_in": info["fadeInTime"] if info else 0.5, "fade_out": info["fadeOutTime"] if info else 0.5,
        })
        if len(curves) < len(bindings):
            logger.info("motion %s: %d of %d curves resolved", name, len(curves), len(bindings))
    return motions


def motion3(m: dict, ids: set[str]) -> dict | None:
    """The motion with only the curves this model has."""
    curves = [c for c in m["curves"] if c[1] in ids]
    if not curves:
        return None
    return {
        "Version": 3,
        "Meta": {
            "Duration": round(m["duration"], 4), "Fps": m["fps"], "Loop": m["loop"], "AreBeziersRestricted": True,
            "FadeInTime": m["fade_in"], "FadeOutTime": m["fade_out"],
            "CurveCount": len(curves), "TotalSegmentCount": sum(c[3] for c in curves),
            "TotalPointCount": sum(c[4] for c in curves), "UserDataCount": 0, "TotalUserDataSize": 0,
        },
        "Curves": [{"Target": t, "Id": i, "Segments": s} for t, i, s, _, _ in curves],
    }


def moc_ids(moc: bytes) -> set[str]:
    """Every id-like string in the .moc3 (parameter and part ids among them)."""
    return {s.decode() for s in re.findall(rb"[A-Za-z_][A-Za-z0-9_.\-]{1,63}", moc)}


def convert(model_dir: Path, out_root: Path, exps: dict) -> tuple[Path, dict, str, set[str]] | None:
    """Writes the model's moc3, textures, physics and expressions; returns what its model3.json needs
    once the motions are known: (folder, model3, file name, ids)."""
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
        return None

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
    logger.info("%s: moc3 v%d, %d tex, physics=%s, %d exp",
                model_dir.name, moc[4], len(textures), bool(rig), len(exp_list))
    # Named after the model, not "model.model3.json": viewers may key per-model settings by file name.
    return out, model3, model_dir.name.removeprefix("live2d_mdl_") + ".model3.json", moc_ids(moc)


def write_motions(out: Path, model3: dict, motions: list[dict], ids: set[str]) -> int:
    """Each motion this model has curves for, as motions/<name>.motion3.json in its own group."""
    groups = {}
    for m in motions:
        data = motion3(m, ids)
        if data is None:
            continue
        rel = f"motions/{m['name']}.motion3.json"
        (out / "motions").mkdir(exist_ok=True)
        (out / rel).write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        groups[m["name"]] = [{"File": rel, "FadeInTime": m["fade_in"], "FadeOutTime": m["fade_out"]}]
    if groups:
        model3["FileReferences"]["Motions"] = groups
    return len(groups)


def main(args: argparse.Namespace) -> int:
    indir, outdir = Path(args.indir), Path(args.outdir)
    assert indir.resolve() != outdir.resolve(), "input and output must differ"
    exps = expressions_by_char(indir)
    models = [m for d in sorted(indir.glob("live2d_mdl_*")) if d.is_dir() and (m := convert(d, outdir, exps))]
    # Motions are shared: their curve targets resolve against every model's ids.
    motions = load_motions(indir, set().union(*(m[3] for m in models)) if models else set())
    logger.info("%d motions", len(motions))
    for out, model3, name, ids in models:
        n = write_motions(out, model3, motions, ids)
        (out / name).write_text(json.dumps(model3, indent=1), encoding="utf-8")
        logger.info("%s: %d motions", out.name, n)
    return 0
