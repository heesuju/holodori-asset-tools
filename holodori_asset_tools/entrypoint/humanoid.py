"""Gives a `models` collection what the game only builds at runtime: the characters' humanoid Avatar, and
every humanoid body motion baked into plain Transform curves, so a viewer needs no muscle solver.

Unity does the humanoid maths (unity/HolodoriHumanoid.cs, run in batchmode): AvatarBuilder from the rest
skeleton (identical on every body) and the `humanoid.bones` map, then HumanPoseHandler poses the rig for
each frame of muscle values decoded here. No game bundle is loaded in Unity, so any Unity 6 works.
Not included, being game scripts rather than clip data: IK, swing-bone physics, helper-bone drivers.
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import re
import shutil
import subprocess
from logging import getLogger
from pathlib import Path

from tqdm import tqdm

from . import models_info as info
from .live2d import clip_curves

logger = getLogger("humanoid")

ACTOR, BODY = "Actor", "Root_Body"  # clip paths start at Root_Body, so the Animator sits on its parent
ROOT_T, ROOT_Q, MUSCLES = range(7, 10), range(10, 14), range(42, 137)
TRANSFORM_WIDTH = {1: 3, 2: 4, 3: 3, 4: 3}
PACKAGES = {"dependencies": {m: "1.0.0" for m in (
    "com.unity.modules.animation", "com.unity.modules.assetbundle", "com.unity.modules.jsonserialize")}}


def skeleton(body: Path) -> dict:
    """The rest pose of the human bones and their ancestors, under Actor/Root_Body."""
    env = info.load(body)
    ts = {o.path_id: o.read() for o in env.objects if o.type.name == "Transform"}
    by_name = {t.m_GameObject.read().m_Name: t for t in ts.values()}
    root = next(t for t in ts.values() if not t.m_Father.path_id)
    keep: dict[str, object] = {}
    for name in info.HUMANOID.values():
        t = by_name[name]
        while t.m_Father.path_id:
            keep.setdefault(t.m_GameObject.read().m_Name, t)
            t = t.m_Father.read()

    def trs(t, name, parent):
        p, r, s = t.m_LocalPosition, t.m_LocalRotation, t.m_LocalScale
        return {"name": name, "parent": parent, "position": [p.x, p.y, p.z],
                "rotation": [r.x, r.y, r.z, r.w], "scale": [s.x, s.y, s.z]}

    bones = [trs(root, BODY, ACTOR)]

    def walk(t, parent):
        for c in t.m_Children:
            c = c.read()
            name = c.m_GameObject.read().m_Name
            if name in keep:
                bones.append(trs(c, name, parent))
                walk(c, name)

    walk(root, BODY)
    return {"root": ACTOR, "bones": bones,
            "human": list(info.HUMANOID), "transforms": list(info.HUMANOID.values())}


def sample(keys: list[tuple], t: float) -> float:
    i = max(0, bisect.bisect_right([k[0] for k in keys], t) - 1)
    t0, a, b, c, d = keys[i]
    if not all(math.isfinite(x) for x in (a, b, c)):
        return d
    dt = max(0.0, t - t0)
    return ((a * dt + b) * dt + c) * dt + d


def motions(src: Path) -> list[dict]:
    """Per humanoid clip in the bundle: frames of RootT, RootQ and the 95 muscles at the clip's rate."""
    out = []
    for o in info.load(src).objects:
        if o.type.name != "AnimationClip":
            continue
        clip = o.read_typetree()
        keys, i, human = clip_curves(clip), 0, {}
        for b in clip["m_ClipBindingConstant"]["genericBindings"]:
            if b["typeID"] == 95:
                human[b["attribute"]] = keys[i]
            i += TRANSFORM_WIDTH.get(b["attribute"], 1) if b["typeID"] == 4 else 1
        if i != len(keys) or not human:
            continue
        mc = clip["m_MuscleClip"]
        fps, length = clip["m_SampleRate"], mc["m_StopTime"] - mc["m_StartTime"]
        frames = max(1, int(round(length * fps)) + 1)
        rest = {6: 1.0, 13: 1.0}  # identity quaternion w for absent curves
        values = []
        for f in range(frames):
            t = mc["m_StartTime"] + min(f / fps, length)
            for a in (*ROOT_T, *ROOT_Q, *MUSCLES):
                values.append(sample(human[a], t) if a in human else rest.get(a, 0.0))
        out.append({"clip": clip["m_Name"], "fps": fps, "loop": bool(mc["m_LoopTime"]),
                    "frames": frames, "values": values})
    return out


def main(args: argparse.Namespace) -> int:
    coll, work = Path(args.collection), Path(args.work).resolve()
    index = json.loads((coll / "index.json").read_text(encoding="utf-8"))
    body = next(e for e in index["models"].values() if e.get("part") == "body" and e.get("face_decals"))
    inp, out, project = work / "in", work / "out", work / "project"
    shutil.rmtree(inp, ignore_errors=True)
    shutil.rmtree(out, ignore_errors=True)
    (inp / "motions").mkdir(parents=True)
    (inp / "skeleton.json").write_text(json.dumps(skeleton(coll / body["path"])), encoding="utf-8")

    baked: dict[str, list[str]] = {}  # animation bundle name -> baked bundle names
    for path in tqdm(index["character_animations"], desc="decode", unit="bundle"):
        name = Path(path).stem
        clips = motions(coll / path)
        for m in clips:
            m["name"] = name if len(clips) == 1 else f"{name}__{m.pop('clip')}".lower()
            m.pop("clip", None)
            (inp / "motions" / f"{m['name']}.json").write_text(json.dumps(m), encoding="utf-8")
            baked.setdefault(name, []).append(m["name"])
    logger.info("%d humanoid clips from %d bundles", sum(map(len, baked.values())), len(baked))

    (project / "Assets" / "Editor").mkdir(parents=True, exist_ok=True)
    (project / "Packages").mkdir(exist_ok=True)
    (project / "Packages" / "manifest.json").write_text(json.dumps(PACKAGES, indent=1), encoding="utf-8")
    (project / "Packages" / "packages-lock.json").unlink(missing_ok=True)
    # Without ProjectVersion.txt Unity treats the folder as a new project and replaces the manifest with
    # its default template, whose registry packages it then tries to download.
    version = Path(args.unity).resolve().parent.parent.name  # .../Hub/Editor/<version>/Editor/Unity.exe
    if re.fullmatch(r"\d+\.\d+\.\d+\w+", version):
        (project / "ProjectSettings").mkdir(exist_ok=True)
        (project / "ProjectSettings" / "ProjectVersion.txt").write_text(f"m_EditorVersion: {version}\n")
    shutil.copyfile(Path(__file__).parent.parent / "unity" / "HolodoriHumanoid.cs",
                    project / "Assets" / "Editor" / "HolodoriHumanoid.cs")
    log = work / "unity.log"
    cmd = [args.unity, "-batchmode", "-nographics", "-quit", "-projectPath", str(project),
           "-executeMethod", "HolodoriHumanoid.Run", "-holodoriIn", str(inp), "-holodoriOut", str(out),
           "-logFile", str(log)]
    logger.info("running Unity (log: %s)", log)
    code = subprocess.call(cmd)
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        if "[holodori]" in line:
            logger.info("unity: %s", line.strip())
    if code != 0 or not (out / "avatar").exists():
        logger.error("Unity failed (exit %d); see %s", code, log)
        return 1

    (coll / "avatars").mkdir(exist_ok=True)
    shutil.copyfile(out / "avatar", coll / "avatars" / "body.bundle")
    index["humanoid"]["avatar"] = "avatars/body.bundle"
    (coll / "animations-baked").mkdir(exist_ok=True)
    for name, outs in baked.items():
        entry = index["animations"].get(name)
        paths = []
        for b in outs:
            if (out / b).exists():
                shutil.copyfile(out / b, coll / "animations-baked" / f"{b}.bundle")
                paths.append(f"animations-baked/{b}.bundle")
        if entry is not None and paths:
            entry["baked"] = paths
    index["humanoid"]["baked_note"] = (
        "`avatar`: the humanoid Avatar Unity's AvatarBuilder makes from this rig (rest pose identical on "
        "every body, importer-default twist/stretch, no translation DoF), on Actor/Root_Body like the game. "
        "Per animation `baked`: the clip's humanoid body motion as plain Transform curves (localRotation of "
        "each human bone, localPosition of the hips; paths from Actor, i.e. 'Root_Body/...'), posed by "
        "Unity's HumanPoseHandler frame by frame. Play it together with the original clip's non-humanoid "
        "curves (face blendshapes, mouth, locators). Not included: IK, swing-bone physics, helper bones.")
    index["clip_sets"] = info.clip_sets(index)
    (coll / "index.json").write_text(json.dumps(index, indent=1, ensure_ascii=False), encoding="utf-8")
    logger.info("avatar + %d baked bundles added to %s", sum(len(e.get("baked", [])) for e in index["animations"].values()), coll)
    return 0
