# holodori-asset-tools

**DOES NOT REQUIRE THE GAME INSTALLED ON PC!!! NO EXTRA SETUP IS REQUIRED.**

Decrypt, encrypt, download and extract holodori (hololive Dreams) game assets.

Inspired by [sssekai](https://github.com/mos9527/sssekai)

**We recommend using `holodori serve` for beginners! It lets you browse and download extracted assets.**

## Install

Download this repository, then run the command

```
pip install -e .
```

## Usage

The command is `holodori`.

Single files are always accepted (decrypt, encrypt, and extract)

```
holodori download ./assets [--filter REGEX] [--catalog octo_list.json]
holodori serve [--host 127.0.0.1] [--port 8000]
holodori decrypt ./in ./out
holodori encrypt ./in ./out [--kind bundle|resource]
holodori extract ./assets ./extracted
holodori live2d ./extracted-live2d ./converted-live2d
holodori models ./assets ./models-3d [--filter REGEX]
holodori humanoid ./models-3d --unity "C:/Program Files/Unity/Hub/Editor/<version>/Editor/Unity.exe"
```

"Serving" the assets allows you to browse in browser the list of assets, and download any one you want.
Each assetbundle and each ACB/AWB/USM resource also gets an `extracted.zip` link that downloads the
asset plus every bundle it requires (per the octo dependency list), extracts them, and returns the
result as `extracted_{name}.zip`. The size shown next to the link is the total download it needs.

Assets live under two flat groups, `assetbundles/` and `resources/`. `download` and
`serve` pull the octo catalog (caching it to `--catalog`), fetch from the CDN and
decrypt on the fly. `decrypt`/`encrypt` operate on local files and key the header
mask on each file's name, so files must be named by their asset name.

`decrypt`, `encrypt` and `extract` accept either a single file or a directory as
their input.

`extract` extracts a given file or directory of assets. The following file types are extracted:
- Assetbundles (files inside are extracted)
- ACB/AWB files (**note: some ACB files rely on a separate AWB file - to extract those both files need to be downloaded**)
- USM files (*extracted as .ivf files, convert to mp4/mov with a converter like ffmpeg or online converter - TODO convert*)

`live2d` converts extracted Live2D models (the `live2d_mdl_*`, `live2d_exp_*` and `live2d_mot_*` folders)
into standard Cubism folders any Cubism 3+ viewer can open: `<model>.model3.json`, `model.moc3`,
`model.physics3.json`, `textures/`, `expressions/*.exp3.json` (each model gets its character's expressions)
and `motions/*.motion3.json` (the shared motions, decoded from Unity AnimationClips).

`models` collects every 3D model as plain (decrypted) Unity bundles, ready for AssetStudio, AssetRipper
or Unity, with nothing converted except the game's stripped Unity version being written back into each
bundle header (so readers need no fallback version): `characters/<char>/` (body and hair bundles), `character-props/`,
`props/`, `models/`, `scenes/`, `animations/` (every `mot_*` bundle) and `dependencies/` (shaders, ramp
textures, effects... shared by the rest), all as `.bundle` files. `index.json` lists, per model, the bundles
to load with it (`requires`), per character body its hair (`hair`: the outfit's own, else the base hair),
and per hair / accessory where it attaches (`attach_to`): parent `bone` under the transform named `to` in
the body (hair) or hair (accessory) with an identity local transform. For game-neutral viewers each model
also gets `attach` (models mounted on it), `hidden_materials`, `decals` (rest-visible, explicit atlas
cells) and `clip_set`, with the clips in top-level `clip_sets`. It also carries what a viewer needs to
draw and animate characters: `render_rules` (alpha/blend/draw order from material keywords), per body
`face_decals` with `face_rules` (the mouth and cheeks are URP decals; 40 mouth shapes in a 5x8 atlas),
`humanoid` (Unity human bone → rig transform map; no humanoid Avatar ships with the game data), and per
motion `kind`/`target`/`clips` with `animation_rules`, listing the ones every body plays in
`character_animations`. Motions are shared by all characters, and the 3D facial expressions
live inside them (eye/brow/iris blendshape and mouth-decal curves). Body motions are Unity humanoid
(muscle) clips; the game builds the humanoid Avatar at runtime, so no Avatar ships with the bodies.

`humanoid` adds what the game only builds at runtime, using a Unity 6 editor in batchmode (any 6000.x; it
loads no game bundle, and needs an activated license): `avatars/body.bundle`, the humanoid Avatar for every
body (one rest pose is shared by all), and `animations-baked/`, every humanoid body motion baked into
plain Transform curves by Unity's HumanPoseHandler, linked from each motion's `baked` entry in index.json.
A viewer then needs no muscle solver. IK, swing-bone physics and helper-bone drivers are game scripts and
are not baked. Re-running `models` keeps these entries.

Octo keys and app versions are fetched from our repository [here](https://github.com/HolodoriDB/holodori-app-protos/tree/main).

The library is usable directly:

```python
from holodori_asset_tools import catalog, crypto

cat = catalog.get("octo_list.json")
entry = cat.assetBundles[0]
raw = crypto.decrypt(open("SEvWEA", "rb").read(), entry.name)
```
