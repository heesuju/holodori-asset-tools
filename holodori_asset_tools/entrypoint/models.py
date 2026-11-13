"""Collects every 3D model bundle, with the animations and everything they depend on, into one
organized folder of plain (decrypted) Unity bundles that AssetStudio / AssetRipper / Unity can open.

Nothing is converted. Facial expressions are not separate assets: they are blendshape (eye, brow, iris)
and mouth-decal curves inside the motion clips, so `animations/` holds them along with the body motions.
Motions are shared by every character (only body type `bdy00` exists).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from logging import getLogger
from pathlib import Path

from tqdm import tqdm

from .. import catalog, crypto

logger = getLogger("models")

CHARACTER = re.compile(r"mdl_chr_drs_(\d+)-(.+)_(body|hair)")
# (folder, pattern) in priority order; the first match wins.
CATEGORIES = [
    ("characters", CHARACTER),
    ("character-props", re.compile(r"mdl_chr_(prp|acc)_.+")),
    ("props", re.compile(r"(fbx_mdl_env_parts|mdl_env_parts)_.+")),
    ("models", re.compile(r"mdl_.+")),
    ("scenes", re.compile(r"(env_3d|shared_sub_scene)_.+")),
    ("animations", re.compile(r"mot_.+")),
]


def category(name: str) -> str | None:
    return next((folder for folder, pat in CATEGORIES if pat.fullmatch(name)), None)


def rel_path(name: str, folder: str) -> str:
    if folder == "characters":
        return f"characters/{CHARACTER.fullmatch(name).group(1)}/{name}"
    return f"{folder}/{name}"


def place(src: Path, dest: Path, link: bool) -> None:
    """Decrypted copy (or hard link) of src at dest; skipped when dest already matches."""
    if dest.exists() and dest.stat().st_size == src.stat().st_size:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    if link:
        if dest.exists():
            dest.unlink()
        try:
            os.link(src, dest)
            return
        except OSError:
            pass  # other drive / filesystem: fall back to copying
    data = src.read_bytes()
    out = crypto.decrypt(data, src.name)
    if out is data:
        shutil.copyfile(src, dest)
    else:
        dest.write_bytes(out)


def main(args: argparse.Namespace) -> int:
    indir, outdir = Path(args.indir), Path(args.outdir)
    if (indir / "assetbundles").is_dir():
        indir = indir / "assetbundles"
    cat = catalog.get(args.catalog)
    names = {e.name for e in cat.assetBundles}

    primary = {n: c for n in sorted(names) if (c := category(n))}
    paths: dict[str, str] = {n: rel_path(n, c) for n, c in primary.items()}
    index: dict = {"revision": cat.revisionId, "models": {}, "animations": {}, "missing": []}
    for name, folder in primary.items():
        deps = [e.name for e in cat.required(name, "assetbundles")[1:]]
        for d in deps:
            paths.setdefault(d, f"dependencies/{d}")
        entry = {"path": paths[name], "requires": [paths[d] for d in deps]}
        if folder == "animations":
            index["animations"][name] = entry
        else:
            m = CHARACTER.fullmatch(name)
            if m:
                entry |= {"character": m.group(1), "outfit": m.group(2), "part": m.group(3)}
            index["models"][name] = {"category": folder, **entry}

    # Which hair goes with which body is game logic we don't have; list the candidates per outfit.
    for name, entry in index["models"].items():
        if entry.get("part") == "body":
            char, outfit = entry["character"], entry["outfit"]
            same = f"mdl_chr_drs_{char}-{outfit}_hair"
            base = [n for n in index["models"] if re.fullmatch(rf"mdl_chr_drs_{char}-base-.+_hair", n)]
            entry["hair"] = [paths[same]] if same in index["models"] else [paths[n] for n in base]

    if args.filter:
        pat = re.compile(args.filter)
        keep = {n for n in primary if pat.search(n)}
        needed = {p for n in keep for p in [paths[n]] + (index["models"].get(n) or index["animations"][n])["requires"]}
    else:
        needed = set(paths.values())

    by_path = {p: n for n, p in paths.items()}
    missing = []
    for p in tqdm(sorted(needed), unit="file"):
        src = indir / by_path[p]
        if not src.exists():
            missing.append(by_path[p])
            continue
        place(src, outdir / p, args.link)
    index["missing"] = sorted(missing)

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
