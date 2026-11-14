"""Game knowledge for `models`' index.json: how characters render, how their faces work and what the
motion bundles hold. Everything per bundle is read from the bundle; the rules below were checked against
the game data (see each comment)."""

from __future__ import annotations

import zlib
from pathlib import Path

from .. import crypto

# Material render state, as the game's shaders (Vision/Actor/*) compile it. The keyword is what selects
# the shader variant, so it decides; RenderType tag and queue agree with it on every character material.
RENDER_RULES = (
    "Per material (read m_ValidKeywords and m_SavedProperties.m_Floats): keyword _SURFACE_TYPE_TRANSPARENT "
    "-> alpha BLEND, factors from _SrcBlend/_DstBlend (UnityEngine.Rendering.BlendMode: 0 Zero, 1 One, "
    "2 DstColor, 5 SrcAlpha, 10 OneMinusSrcAlpha); keyword _ALPHATEST_ON -> alpha MASK with cutoff _Cutoff "
    "(0.5 when absent), also when combined with BLEND; neither -> OPAQUE and the base map's alpha is "
    "ignored (the _col/_col_alp alpha is a coverage mask only used by the cutout/blend materials sharing "
    "that texture). Shader Vision/Actor/Decal: _ALPHAMODULATE_ON -> multiply (DstColor, Zero), else "
    "premultiplied alpha (One, OneMinusSrcAlpha). _Cull 0 = double sided, 2 = back-face culling. _ZWrite as "
    "given. Draw order = m_CustomRenderQueue (-1 = 2000): e.g. eye whites 2000, iris 2002 (ZWrite 0, drawn "
    "over the whites), hair 2005, cutout 2450, transparent 2600. Skip any submesh whose material uses shader "
    "Hidden/Vision/SubMeshOutline (an inverted-hull outline duplicate). Draw only LOD0 renderers. Meshes "
    "with bone indices but no bone weights (Geo_Iris, Geo_Brow) are single-bone skinned: weight 1.0."
)

# FacialDecal/* DecalProjectors under the head; atlas layout checked on the texture: 40 mouths in a 5x8
# grid from the top-left, then a column of cheek/pale/blush tiles. Across 120 clips the mouth weights are
# only ever 0 or 1 with at most one set: they select a mouth.
FACE_RULES = (
    "Eyes, brows and iris are meshes (Geo_Eye, Geo_Brow, Geo_Iris). The mouth and cheek effects are URP "
    "DecalProjectors listed in each body's `face_decals`: project `material` from the projector transform "
    "at `path` (relative to the body root) along its local +Z, through a box of `size` centered at `offset` "
    "(local space, transform scale applies), onto the body mesh; decal uv = (local.xy / size.xy + 0.5) * "
    "uv_scale + uv_bias. Opacity = fade_factor (cheek/pale/blush start at 0 and are animated). For the "
    "mouth: shape i of `mouth.shapes` sits at uv_bias = (i % columns * uv_scale.x, 1 - (i // columns + 1) * "
    "uv_scale.y); the motion curves `weights.w<i>` (0 or 1, at most one at a time) pick the shape, none "
    "set = `mouth.neutral`."
)

ANIMATION_RULES = (
    "Every body plays every bundle in `character_animations` (motions are shared; only body type bdy00 "
    "exists). Curve targets are hashed: binding.path = crc32 of the transform path from the actor root "
    "`Root_Body` (e.g. 'Root_Body/jnt_C_root00_00/...'; hair bones sit under '.../jnt_C_head00_00/"
    "jnt_C_headHair00_00/...' per the attach rule). By binding.typeID: 4 Transform (attribute 1 position, "
    "2 rotation quaternion, 3 scale, 4 euler degrees; 3 or 4 curves), 137 SkinnedMeshRenderer blendshape "
    "(attribute = the mesh's blendshape nameHash; values 0-100), 114 MonoBehaviour field (attribute = crc32 "
    "of the field path, e.g. 'weights.w5' on VisionActorMouthDecal, 'm_FadeFactor' on DecalProjector), 95 "
    "Animator humanoid muscle (attribute = Unity HumanTrait muscle index; needs a humanoid avatar and solver: "
    "the game builds the avatar at runtime, none ships). Curves are stored streamed, then dense, then "
    "constant, in binding order. Bindings whose path is not on the model (other outfits' hair bones) are "
    "ignored, as in Unity. `define` is the game's VisionActorMotionDefine: layer, additive, blend times."
)


# Unity HumanBodyBones -> transform name. No humanoid Avatar ships (the game builds it at runtime), so
# this is mapped from the bone names; every body shares these 52 core bones. The rig has no UpperChest,
# Jaw or Toes. `check_humanoid` verifies the sides against bone positions.
_FINGERS = {"Thumb": "Thumb", "Index": "Index", "Middle": "Middle", "Ring": "Ring", "Little": "Pinky"}
HUMANOID = {
    "Hips": "jnt_C_hips00_00", "Spine": "jnt_C_spine00_00", "Chest": "jnt_C_spine00_01",
    "Neck": "jnt_C_neck00_00", "Head": "jnt_C_head00_00",
    **{f"{side}{bone}": f"jnt_{s}_{name}00_00"
       for side, s in (("Left", "L"), ("Right", "R"))
       for bone, name in (("Shoulder", "shoulder"), ("UpperArm", "upperArm"), ("LowerArm", "foreArm"),
                          ("Hand", "hand"), ("UpperLeg", "thigh"), ("LowerLeg", "leg"), ("Foot", "foot"),
                          ("Eye", "eye"))},
    **{f"{side}{finger}{part}": f"jnt_{s}_finger{name}00_{i:02d}"
       for side, s in (("Left", "L"), ("Right", "R"))
       for finger, name in _FINGERS.items()
       for i, part in enumerate(("Proximal", "Intermediate", "Distal"))},
}
HUMANOID_NOTE = (
    "`humanoid.bones` maps Unity HumanBodyBones names to this rig's transforms (the same for every body). "
    "It is derived from bone names, not from a shipped Avatar: none ships with the bodies, and the Avatars in "
    "some hair bundles are generic (hair physics bones only, no human bones). jnt_C_root00_00 is the root above "
    "the hips. Humanoid curve attributes (typeID 95): 0-2 MotionT, 3-6 MotionQ (constant identity in these "
    "clips: no root motion), 7-9 RootT (body center, y ~0.8-1.4), 10-13 RootQ, 14-41 IK goals T then Q for "
    "LeftFoot, RightFoot, LeftHand, RightHand, 42-136 the 95 HumanTrait.MuscleName muscles in order (55 body, "
    "then 40 finger). Checked on the clips: 0-6 identity, 7-9 a body-center height, and 10-13, 17-20, "
    "24-27, 31-34, 38-41 unit quaternions."
)


def check_humanoid(src: Path) -> list[str]:
    """Problems with HUMANOID on this body: missing bones, or Left bones not on the character's left."""
    env = load(src)
    ts = {o.read().m_GameObject.read().m_Name: o.read() for o in env.objects if o.type.name == "Transform"}
    missing = [f"{k} -> {v} missing" for k, v in HUMANOID.items() if v not in ts]

    def world_x(t) -> float:
        # Only the sign matters; accumulate translations through parents' rotations.
        import math

        def rot(q, v):
            x, y, z, w = q
            ux, uy, uz = 2 * (y * v[2] - z * v[1]), 2 * (z * v[0] - x * v[2]), 2 * (x * v[1] - y * v[0])
            return (v[0] + w * ux + y * uz - z * uy, v[1] + w * uy + z * ux - x * uz,
                    v[2] + w * uz + x * uy - y * ux)

        chain = []
        while t is not None:
            chain.append(t)
            t = t.m_Father.read() if t.m_Father.path_id else None
        p, q = (0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0)
        for t in reversed(chain):
            lp, lr = t.m_LocalPosition, t.m_LocalRotation
            d = rot(q, (lp.x, lp.y, lp.z))
            p = (p[0] + d[0], p[1] + d[1], p[2] + d[2])
            a, b = q, (lr.x, lr.y, lr.z, lr.w)
            q = (a[3] * b[0] + a[0] * b[3] + a[1] * b[2] - a[2] * b[1],
                 a[3] * b[1] - a[0] * b[2] + a[1] * b[3] + a[2] * b[0],
                 a[3] * b[2] + a[0] * b[1] - a[1] * b[0] + a[2] * b[3],
                 a[3] * b[3] - a[0] * b[0] - a[1] * b[1] - a[2] * b[2])
        return p[0] if math.isfinite(p[0]) else 0.0

    # Unity characters face +Z, so the character's left is -X.
    wrong = [k for k, v in HUMANOID.items() if k.startswith("Left") and v in ts and world_x(ts[v]) >= 0]
    return missing + [f"{k} not at -X" for k in wrong]


def blend_shape_names(src: Path) -> set[str]:
    return {c.name for o in load(src).objects if o.type.name == "Mesh"
            for m in [o.read()] if m.m_Shapes for c in m.m_Shapes.channels}


def blend_shape_fixes(clip_names: list[str], mesh_names: set[str]) -> dict[str, str]:
    """{attribute: mesh channel} for clip channels that exist on no mesh. Unity binds blend shapes by
    crc32 of the full channel name, so these would not play; 4 clips name them b_brow1/b_eye1/b_iris1
    where every face mesh has b_brow/b_eye/b_iris with the same target names."""
    out = {}
    for name in clip_names:
        if name in mesh_names:
            continue
        group, _, target = name.partition(".")
        fixed = f"{group.rstrip('0123456789')}.{target}"
        if fixed in mesh_names:
            out[str(zlib.crc32(name.encode()))] = fixed
    return out


def outline_materials(bundle: Path, shader_bundle: Path) -> list[str]:
    """Materials in `bundle` using the inverted-hull outline shader (drawn as a duplicate submesh)."""
    import UnityPy

    from .models import with_unity_version

    raw = [with_unity_version(crypto.decrypt(p.read_bytes(), p.name)) for p in (bundle, shader_bundle)]
    env = UnityPy.load(*raw)
    names = []
    for o in env.objects:
        if o.type.name == "Material":
            try:
                if o.read().m_Shader.read().m_ParsedForm.m_Name == "Hidden/Vision/SubMeshOutline":
                    names.append(o.read().m_Name)
            except Exception:
                pass
    return names


def neutral_decals(decals: list[dict]) -> list[dict]:
    """face_decals in the viewer's game-neutral form: only those visible at rest, atlas cells explicit."""
    out = []
    for d in decals:
        if not d.get("enabled") or d["fade_factor"] <= 0:
            continue
        g = {k: d[k] for k in ("name", "path", "material", "size", "offset", "uv_scale", "uv_bias")}
        g["opacity"] = d["fade_factor"]
        m = d.get("mouth")
        if m and m["columns"]:
            su, sv = d["uv_scale"]
            g["atlas"] = {
                "cells": [[round(i % m["columns"] * su, 6), round(1 - (i // m["columns"] + 1) * sv, 6)]
                          for i in range(m["shapes"])],
                "default": m["neutral"],
                "fields": [f"weights.w{i}" for i in range(m["shapes"])],
            }
        out.append(g)
    return out


def clip_sets(index: dict) -> dict:
    """The viewer's clip_sets: every character motion, as its baked Transform clip (with the original's
    blend-shape / MonoBehaviour curves alongside) when it has one, else the original clip."""
    items = []
    for path in index["character_animations"]:
        name = Path(path).stem
        entry = index["animations"].get(name, {})
        baked = entry.get("baked", [])
        item = {"name": name, "root": "Root_Body"}
        item |= {"path": baked[0], "curves_from": path} if baked else {"path": path}
        if entry.get("blend_shape_fixes"):
            item["blend_shapes"] = entry["blend_shape_fixes"]
        items.append(item)
    return {"character": items}


def load(src: Path):
    import UnityPy

    from .models import with_unity_version

    return UnityPy.load(with_unity_version(crypto.decrypt(src.read_bytes(), src.name)))


def _vec(v: dict) -> list[float]:
    return [round(v[k], 6) for k in ("x", "y", "z", "w") if k in v]


def _path(t) -> str:
    names = []
    while t.m_Father.path_id:
        names.append(t.m_GameObject.read().m_Name)
        t = t.m_Father.read()
    return "/".join(reversed(names))


def face_decals(src: Path) -> list[dict]:
    env = load(src)
    transforms = {o.read().m_GameObject.path_id: o.read() for o in env.objects if o.type.name == "Transform"}
    by_go: dict[int, dict] = {}
    for o in env.objects:
        if o.type.name != "MonoBehaviour":
            continue
        mb = o.read()
        cls = mb.m_Script.read().m_ClassName
        if cls not in ("DecalProjector", "VisionActorMouthDecal"):
            continue
        tt = o.read_typetree()
        d = by_go.setdefault(mb.m_GameObject.path_id, {})
        if cls == "DecalProjector":
            mat = mb.m_Material.read().m_Name if tt["m_Material"]["m_PathID"] else None
            d.update({
                "name": mb.m_GameObject.read().m_Name,
                "path": _path(transforms[mb.m_GameObject.path_id]),
                "material": mat,
                "size": _vec(tt["m_Size"]), "offset": _vec(tt["m_Offset"]),
                "uv_scale": _vec(tt["m_UVScale"]), "uv_bias": _vec(tt["m_UVBias"]),
                "fade_factor": tt["m_FadeFactor"], "priority": tt["m_DecalPriority"],
                "enabled": bool(tt["m_Enabled"]),
            })
        else:
            d["_weights"] = len(tt["weights"])
    out = []
    for d in by_go.values():
        n = d.pop("_weights", 0)
        if "name" not in d:
            continue
        if n:
            cols, rows = int(1 / d["uv_scale"][0]), int(round(1 / d["uv_scale"][1]))
            neutral = round(d["uv_bias"][0] / d["uv_scale"][0]) + cols * round(
                (1 - d["uv_bias"][1]) / d["uv_scale"][1] - 1)
            d["mouth"] = {"shapes": n, "columns": cols if cols * rows == n else None, "neutral": neutral,
                          "field": "weights.w<i>"}
        out.append(d)
    return sorted(out, key=lambda d: d["name"])


def _target(name: str) -> str:
    for key, target in (("_chr_", "character"), ("_cam_", "camera"), ("_env_", "environment"),
                        ("_plt_", "platform"), ("_ui_", "ui")):
        if key in name:
            return target
    return "other"


def motion_info(name: str, src: Path) -> dict:
    env = load(src)
    info: dict = {"kind": name.split("_")[1], "target": _target(name), "clips": []}
    hashed = {}
    for o in env.objects:
        if o.type.name == "MonoBehaviour":
            tt = o.read_typetree()
            if "baseAnimation" in tt:
                info["define"] = {k: tt[k] for k in ("layerName", "isAdditive", "transitionDurationIn",
                                                     "transitionDurationOut") if k in tt}
                for b in tt["baseAnimation"]["bindings"]:
                    hashed[zlib.crc32(b["path"].encode())] = b["path"]
                    info.setdefault("_blend_shapes", []).extend(
                        p.removeprefix("blendShape.") for p in b["properties"] if p.startswith("blendShape."))
        elif o.type.name == "AnimationClip":
            c = o.read_typetree()
            mc = c["m_MuscleClip"]
            kinds = {95: "humanoid", 4: "transform", 137: "blendshape", 114: "component"}
            counts: dict[str, int] = {}
            for b in c["m_ClipBindingConstant"]["genericBindings"]:
                k = kinds.get(b["typeID"], f"type{b['typeID']}")
                counts[k] = counts.get(k, 0) + 1
            info["clips"].append({
                "name": c["m_Name"], "length": round(mc["m_StopTime"] - mc["m_StartTime"], 4),
                "fps": c["m_SampleRate"], "loop": bool(mc["m_LoopTime"]), "bindings": counts,
            })
    roots = {p.split("/")[0] for p in hashed.values()}
    if roots:
        info["root"] = sorted(roots)[0] if len(roots) == 1 else sorted(roots)
    return info
