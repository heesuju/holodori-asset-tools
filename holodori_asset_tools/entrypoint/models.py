"""Collects every 3D model bundle, with the animations and everything they depend on, into one
organized folder of plain (decrypted) Unity bundles that AssetStudio / AssetRipper / Unity can open.

Nothing is converted; the only change is the game's stripped Unity version ("0.0.0") being filled in
in each bundle header, so readers don't need to be told which version to assume. Facial expressions are
not separate assets: they are blendshape (eye, brow, iris) and mouth-decal curves inside the motion
clips, so `animations/` holds them along with the body motions. Motions are shared by every character
(only body type `bdy00` exists). index.json also carries the game knowledge a viewer needs (see
models_info): render, face, animation and humanoid rules, plus per-body face decals and per-motion clips.
"""

from __future__ import annotations

import argparse
import json
import re
import struct
from logging import getLogger
from pathlib import Path

from tqdm import tqdm

from .. import catalog, crypto
from . import models_info as info
from .extract import UNITY_VERSION

logger = getLogger("models")

CHARACTER = re.compile(r"mdl_chr_drs_(\d+)-(.+)_(body|hair)")
# (folder, pattern) in priority order; the first match wins. Hair is its own model folder: it is mounted
# on bodies through `attach`, so viewers can leave it out of the browsable models.
CATEGORIES = [
    ("hair", re.compile(r"mdl_chr_drs_\d+-.+_hair")),
    ("characters", CHARACTER),
    ("character-props", re.compile(r"mdl_chr_(prp|acc)_.+")),
    ("props", re.compile(r"(fbx_mdl_env_parts|mdl_env_parts)_.+")),
    ("models", re.compile(r"mdl_.+")),
    ("scenes", re.compile(r"(env_3d|shared_sub_scene)_.+")),
    ("animations", re.compile(r"mot_.+")),
]


def category(name: str) -> str | None:
    return next((folder for folder, pat in CATEGORIES if pat.fullmatch(name)), None)


EXT = ".bundle"

# How the game assembles a character, checked against every bundle: the hair's bind pose for
# jnt_C_headHair00_00 equals the body's for jnt_C_head00_00 (to <0.5 mm, all pairs), and in every hair
# bundle that bone sits at identity under the root. Accessories hang from same-named locators in the hair.
HAIR_BONE, HEAD_BONE = "jnt_C_headHair00_00", "jnt_C_head00_00"
ACC_LOCATOR = re.compile(r"loc_[CLR]_(hair|face)Acc\d+_\d+_acc")
ATTACH_RULES = (
    "Parent the part's `bone` under the transform named `to` in the `of` model, with an identity local "
    "transform (position 0, rotation identity, scale 1). Skinned meshes then line up with their bind pose. "
    "Hair attaches to the body's head; accessories attach to the same-named locator in the hair."
)


def rel_path(name: str, folder: str) -> str:
    if folder in ("characters", "hair"):
        return f"{folder}/{CHARACTER.fullmatch(name).group(1)}/{name}{EXT}"
    return f"{folder}/{name}{EXT}"


def transform_names(src: Path) -> set[str]:
    import UnityPy

    env = UnityPy.load(with_unity_version(crypto.decrypt(src.read_bytes(), src.name)))
    return {o.read().m_GameObject.read().m_Name for o in env.objects if o.type.name == "Transform"}


def attach_rule(name: str, src: Path) -> dict | None:
    """Where the hair / accessory in bundle `name` attaches, if its bones are there."""
    if not src.exists():
        return None
    names = transform_names(src)
    if name.endswith("_hair") and HAIR_BONE in names:
        return {"bone": HAIR_BONE, "to": HEAD_BONE, "of": "body", "local": "identity"}
    if name.startswith("mdl_chr_acc_"):
        loc = sorted(n for n in names if ACC_LOCATOR.fullmatch(n))
        if loc:
            return {"bone": loc[0], "to": loc[0], "of": "hair", "local": "identity"}
    return None


def with_unity_version(data: bytes, version: str = UNITY_VERSION) -> bytes:
    """The UnityFS bundle with its stripped engine version ("0.0.0") replaced, so readers need no
    fallback version (the serialized files inside, also "0.0.0", inherit the bundle's)."""
    if not data.startswith(b"UnityFS\0"):
        return data
    player_end = data.index(b"\0", 12)
    engine_end = data.index(b"\0", player_end + 1)
    if data[player_end + 1:engine_end] != b"0.0.0":
        return data
    fields = engine_end + 1  # size (8), blocks info sizes (4 + 4), flags (4)
    (fmt,) = struct.unpack_from(">I", data, 8)
    aligned = lambda n: n + (-n % 16) if fmt >= 7 else n
    body = data[aligned(fields + 20):]  # blocks info and data: positioned by 16-byte alignment only
    header = data[:player_end + 1] + version.encode() + b"\0" + data[fields:fields + 20]
    header += b"\0" * (aligned(len(header)) - len(header))
    out = bytearray(header + body)
    struct.pack_into(">q", out, player_end + 1 + len(version) + 1, len(out))
    return bytes(out)


def place(src: Path, dest: Path) -> None:
    """Decrypted copy of src at dest with the Unity version baked in; skipped when dest already matches."""
    out = with_unity_version(crypto.decrypt(src.read_bytes(), src.name))
    if dest.exists() and dest.stat().st_size == len(out):
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(out)


def main(args: argparse.Namespace) -> int:
    indir, outdir = Path(args.indir), Path(args.outdir)
    if (indir / "assetbundles").is_dir():
        indir = indir / "assetbundles"
    cat = catalog.get(args.catalog)
    names = {e.name for e in cat.assetBundles}

    primary = {n: c for n in sorted(names) if (c := category(n))}
    paths: dict[str, str] = {n: rel_path(n, c) for n, c in primary.items()}
    index: dict = {"revision": cat.revisionId, "attach_rules": ATTACH_RULES,
                   "render_rules": info.RENDER_RULES, "face_rules": info.FACE_RULES,
                   "animation_rules": info.ANIMATION_RULES,
                   "humanoid": {"note": info.HUMANOID_NOTE, "bones": info.HUMANOID},
                   "character_animations": [], "models": {}, "animations": {}, "missing": []}
    for name, folder in primary.items():
        deps = [e.name for e in cat.required(name, "assetbundles")[1:]]
        for d in deps:
            paths.setdefault(d, f"dependencies/{d}{EXT}")
        entry = {"path": paths[name], "requires": [paths[d] for d in deps]}
        if folder == "animations":
            index["animations"][name] = entry
        else:
            m = CHARACTER.fullmatch(name)
            if m:
                entry |= {"character": m.group(1), "outfit": m.group(2), "part": m.group(3)}
            index["models"][name] = {"category": folder, **entry}

    # Which hair goes with which body is game logic we don't have: the outfit's own hair when it has one,
    # otherwise the character's base hair.
    mesh_blend_shapes: set[str] = set()
    for name, entry in index["models"].items():
        if entry.get("part") == "body":
            char, outfit = entry["character"], entry["outfit"]
            same = f"mdl_chr_drs_{char}-{outfit}_hair"
            if same in index["models"]:
                entry["hair"] = [{"path": paths[same], "match": "outfit"}]
            else:
                entry["hair"] = [{"path": paths[n], "match": "base"} for n in index["models"]
                                 if re.fullmatch(rf"mdl_chr_drs_{char}-base-.+_hair", n)]
            if (indir / name).exists():
                entry["face_decals"] = info.face_decals(indir / name)
                mesh_blend_shapes |= info.blend_shape_names(indir / name)
                for problem in info.check_humanoid(indir / name):
                    logger.warning("%s: humanoid map: %s", name, problem)
        elif entry.get("part") == "hair" or name.startswith("mdl_chr_acc_"):
            if rule := attach_rule(name, indir / name):
                entry["attach_to"] = rule

    for name, entry in tqdm(index["animations"].items(), desc="animations", unit="bundle"):
        if (indir / name).exists():
            entry.update(info.motion_info(name, indir / name))
            if fixes := info.blend_shape_fixes(entry.pop("_blend_shapes", []), mesh_blend_shapes):
                entry["blend_shape_fixes"] = fixes
            if entry["target"] == "character" and entry["clips"]:
                index["character_animations"].append(entry["path"])

    # The viewer's game-neutral fields (it reads only these): mounts, hidden materials, rest decals, clips.
    outline_dep = paths.get("submeshoutlinematerial")
    outline = (info.outline_materials(indir / "submeshoutlinematerial", indir / "remote-shader")
               if (indir / "submeshoutlinematerial").exists() and (indir / "remote-shader").exists() else [])
    for name, entry in index["models"].items():
        if outline and outline_dep in entry["requires"]:
            entry["hidden_materials"] = outline
        if entry.get("part") == "body":
            entry["attach"] = [{"path": h["path"], "bone": HAIR_BONE, "to": HEAD_BONE} for h in entry["hair"]]
            entry["decals"] = info.neutral_decals(entry.get("face_decals", []))
            entry["clip_set"] = "character"

    if args.filter:
        pat = re.compile(args.filter)
        keep = {n for n in primary if pat.search(n)}
        needed = {p for n in keep for p in [paths[n]] + (index["models"].get(n) or index["animations"][n])["requires"]}
    else:
        needed = set(paths.values())

    for p in list(needed):
        # Earlier layouts: files without an extension, hair inside characters/<id>/.
        for legacy in (outdir / p.removesuffix(EXT), outdir / ("characters/" + p.removeprefix("hair/"))):
            if legacy != outdir / p and legacy.is_file() and not (outdir / p).exists():
                (outdir / p).parent.mkdir(parents=True, exist_ok=True)
                legacy.rename(outdir / p)

    by_path = {p: n for n, p in paths.items()}
    missing = []
    for p in tqdm(sorted(needed), unit="file"):
        src = indir / by_path[p]
        if not src.exists():
            missing.append(by_path[p])
            continue
        place(src, outdir / p)
    index["missing"] = sorted(missing)

    # Keep what `holodori humanoid` added to a previous index, while its files are still there.
    old_index = outdir / "index.json"
    if old_index.exists():
        old = json.loads(old_index.read_text(encoding="utf-8"))
        for key in ("avatar", "baked_note"):
            if key in old.get("humanoid", {}) and (key != "avatar" or (outdir / old["humanoid"][key]).exists()):
                index["humanoid"][key] = old["humanoid"][key]
        for name, entry in old.get("animations", {}).items():
            baked = [p for p in entry.get("baked", []) if (outdir / p).exists()]
            if baked and name in index["animations"]:
                index["animations"][name]["baked"] = baked
    index["clip_sets"] = info.clip_sets(index)

    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "index.json").write_text(json.dumps(index, indent=1, ensure_ascii=False), encoding="utf-8")
    counts = {}
    for p in needed:
        counts[p.split("/")[0]] = counts.get(p.split("/")[0], 0) + 1
    logger.info("%s", ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())))
    if missing:
        logger.warning("%d bundles missing from %s (download them first): %s",
                       len(missing), indir, ", ".join(missing[:20]))
    return 0
