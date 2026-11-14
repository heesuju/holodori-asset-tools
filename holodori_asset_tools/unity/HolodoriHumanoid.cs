// Run by `holodori humanoid` in a Unity editor (batchmode). Builds the characters' humanoid Avatar from
// the exported rest skeleton with Unity's own AvatarBuilder, then turns each exported muscle clip into a
// plain Transform-curve AnimationClip by posing the rig with HumanPoseHandler (Unity's humanoid maths)
// frame by frame. Everything is built into AssetBundles.
//
// Args: -holodoriIn <dir with skeleton.json and motions/*.json> -holodoriOut <bundle output dir>
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEngine;

public static class HolodoriHumanoid
{
    [Serializable] class Bone { public string name; public string parent; public float[] position; public float[] rotation; public float[] scale; }
    [Serializable] class Skeleton { public string root; public Bone[] bones; public string[] human; public string[] transforms; }
    // values: per frame RootT (3), RootQ (4), then the 95 muscles in HumanTrait.MuscleName order.
    [Serializable] class Motion { public string name; public float fps; public bool loop; public int frames; public float[] values; }

    const int Stride = 3 + 4 + 95;
    const string Work = "Assets/HolodoriHumanoid";

    static string Arg(string key)
    {
        var args = Environment.GetCommandLineArgs();
        int i = Array.IndexOf(args, key);
        if (i < 0 || i + 1 >= args.Length) throw new ArgumentException("missing " + key);
        return args[i + 1];
    }

    static string PathOf(Transform t, Transform root)
    {
        var names = new List<string>();
        for (; t != root; t = t.parent) names.Add(t.name);
        names.Reverse();
        return string.Join("/", names);
    }

    public static void Run()
    {
        string input = Arg("-holodoriIn"), output = Arg("-holodoriOut");
        var skel = JsonUtility.FromJson<Skeleton>(File.ReadAllText(Path.Combine(input, "skeleton.json")));
        if (HumanTrait.MuscleCount != 95) throw new Exception("unexpected muscle count " + HumanTrait.MuscleCount);

        // The rig at rest, under the actor root the game's Animator sits on.
        var actor = new GameObject(skel.root);
        var byName = new Dictionary<string, Transform> { [skel.root] = actor.transform };
        foreach (var b in skel.bones)
        {
            var t = new GameObject(b.name).transform;
            t.SetParent(byName[b.parent], false);
            t.localPosition = new Vector3(b.position[0], b.position[1], b.position[2]);
            t.localRotation = new Quaternion(b.rotation[0], b.rotation[1], b.rotation[2], b.rotation[3]);
            t.localScale = new Vector3(b.scale[0], b.scale[1], b.scale[2]);
            byName[b.name] = t;
        }

        var desc = new HumanDescription
        {
            human = skel.human.Select((h, i) => new HumanBone
            {
                // humanoid.bones keys are HumanBodyBones names; AvatarBuilder wants HumanTrait.BoneName.
                humanName = HumanTrait.BoneName[(int)Enum.Parse(typeof(HumanBodyBones), h)],
                boneName = skel.transforms[i],
                limit = new HumanLimit { useDefaultValues = true },
            }).ToArray(),
            skeleton = actor.GetComponentsInChildren<Transform>().Select(t => new SkeletonBone
            {
                name = t.name, position = t.localPosition, rotation = t.localRotation, scale = t.localScale,
            }).ToArray(),
            // Unity's model-importer defaults.
            upperArmTwist = 0.5f, lowerArmTwist = 0.5f, upperLegTwist = 0.5f, lowerLegTwist = 0.5f,
            armStretch = 0.05f, legStretch = 0.05f, feetSpacing = 0f, hasTranslationDoF = false,
        };
        var avatar = AvatarBuilder.BuildHumanAvatar(actor, desc);
        if (!avatar.isValid || !avatar.isHuman) throw new Exception("avatar invalid: " + avatar.isValid + " human " + avatar.isHuman);
        avatar.name = "holodori_body_avatar";

        if (AssetDatabase.IsValidFolder(Work)) AssetDatabase.DeleteAsset(Work);
        AssetDatabase.CreateFolder("Assets", "HolodoriHumanoid");
        AssetDatabase.CreateAsset(avatar, Work + "/avatar.asset");
        AssetImporter.GetAtPath(Work + "/avatar.asset").assetBundleName = "avatar";

        // Bones the clips drive: every mapped human bone (rotation) and the hips (position).
        var handler = new HumanPoseHandler(avatar, actor.transform);
        var human = skel.transforms.Select(n => byName[n]).ToArray();
        var hips = byName[skel.transforms[Array.IndexOf(skel.human, "Hips")]];
        var pose = new HumanPose();
        handler.GetHumanPose(ref pose);
        var animator = actor.AddComponent<Animator>();
        animator.avatar = avatar;
        Debug.Log($"[holodori] rest bodyPosition {pose.bodyPosition:F4} bodyRotation {pose.bodyRotation:F4} hips {hips.position:F4} humanScale {animator.humanScale:F4}");

        var files = Directory.GetFiles(Path.Combine(input, "motions"), "*.json");
        int done = 0;
        foreach (var file in files)
        {
            var m = JsonUtility.FromJson<Motion>(File.ReadAllText(file));
            var curves = new Dictionary<string, AnimationCurve[]>();
            foreach (var t in human) curves[PathOf(t, actor.transform)] = new[] { new AnimationCurve(), new AnimationCurve(), new AnimationCurve(), new AnimationCurve() };
            var hipCurves = new[] { new AnimationCurve(), new AnimationCurve(), new AnimationCurve() };
            var muscles = new float[95];
            for (int f = 0; f < m.frames; f++)
            {
                int o = f * Stride;
                pose.bodyPosition = new Vector3(m.values[o], m.values[o + 1], m.values[o + 2]);
                pose.bodyRotation = new Quaternion(m.values[o + 3], m.values[o + 4], m.values[o + 5], m.values[o + 6]).normalized;
                Array.Copy(m.values, o + 7, muscles, 0, 95);
                pose.muscles = muscles;
                handler.SetHumanPose(ref pose);
                float time = f / m.fps;
                foreach (var t in human)
                {
                    var c = curves[PathOf(t, actor.transform)];
                    var q = t.localRotation;
                    c[0].AddKey(time, q.x); c[1].AddKey(time, q.y); c[2].AddKey(time, q.z); c[3].AddKey(time, q.w);
                }
                var p = hips.localPosition;
                hipCurves[0].AddKey(time, p.x); hipCurves[1].AddKey(time, p.y); hipCurves[2].AddKey(time, p.z);
            }
            var clip = new AnimationClip { name = m.name, frameRate = m.fps };
            foreach (var kv in curves)
            {
                string[] comp = { "x", "y", "z", "w" };
                for (int i = 0; i < 4; i++) clip.SetCurve(kv.Key, typeof(Transform), "localRotation." + comp[i], Linear(kv.Value[i]));
            }
            string hipPath = PathOf(hips, actor.transform);
            clip.SetCurve(hipPath, typeof(Transform), "localPosition.x", Linear(hipCurves[0]));
            clip.SetCurve(hipPath, typeof(Transform), "localPosition.y", Linear(hipCurves[1]));
            clip.SetCurve(hipPath, typeof(Transform), "localPosition.z", Linear(hipCurves[2]));
            clip.EnsureQuaternionContinuity();
            var settings = AnimationUtility.GetAnimationClipSettings(clip);
            settings.loopTime = m.loop;
            AnimationUtility.SetAnimationClipSettings(clip, settings);
            string asset = $"{Work}/{m.name}.anim";
            AssetDatabase.CreateAsset(clip, asset);
            AssetImporter.GetAtPath(asset).assetBundleName = m.name;
            if (++done % 50 == 0) Debug.Log($"[holodori] baked {done}/{files.Length}");
        }
        handler.Dispose();
        AssetDatabase.SaveAssets();

        Directory.CreateDirectory(output);
        BuildPipeline.BuildAssetBundles(output, BuildAssetBundleOptions.ChunkBasedCompression, BuildTarget.StandaloneWindows64);
        Debug.Log($"[holodori] done: avatar + {done} clips -> {output}");
    }

    static AnimationCurve Linear(AnimationCurve c)
    {
        for (int i = 0; i < c.length; i++)
        {
            AnimationUtility.SetKeyLeftTangentMode(c, i, AnimationUtility.TangentMode.Linear);
            AnimationUtility.SetKeyRightTangentMode(c, i, AnimationUtility.TangentMode.Linear);
        }
        return c;
    }
}
