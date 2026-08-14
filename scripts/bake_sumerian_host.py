"""Bake a Sumerian Host's bone-driven viseme poses into morph targets.

Sumerian Host characters carry ZERO morph targets: their runtime animates
faces with facial bones, driven by pose clips in lipsync.glb whose names are
Polly's exact viseme codes (p, a, E, sil, ...). The LCDash avatar runtime is
morph-driven, so this bakes each viseme pose into a shape key named
``viseme_<code>`` (plus ``blink``), producing a GLB the runtime can drive
directly from Polly speech marks -- no approximation through ARKit names.

Morphs bake in rest space and three.js applies morphs BEFORE skinning, so
the baked shapes remain valid if a skeletal idle is played on top.

    blender --background --factory-startup --python bake_sumerian_host.py --
        character.gltf lipsync.glb blink.glb out.glb
"""
import sys

import bpy

argv = sys.argv[sys.argv.index("--") + 1:]
char_path, lipsync_path, blink_path, out_path = argv

# Meshes whose vertices actually move for visemes and blinks. Baking the
# whole body would add useless deltas to every one of 17 meshes.
FACE_MESH_HINTS = ("head", "mouth", "eyelash", "eyebrow", "eyes_inner", "eyes_outer")

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=char_path)

char_armature = next(o for o in bpy.data.objects if o.type == "ARMATURE")
char_meshes = [o for o in bpy.data.objects if o.type == "MESH"]
face_meshes = [
    o for o in char_meshes
    if any(h in o.name.lower() for h in FACE_MESH_HINTS)
]
print("FACE_MESHES:", [o.name for o in face_meshes])

# Importing an animation GLB brings its own skeleton copy carrying the
# actions. Bone names match the character's skeleton, so the actions can be
# assigned to the character armature directly; the imported duplicates are
# then deleted.
def import_actions(path):
    before_objects = set(bpy.data.objects)
    before_actions = set(bpy.data.actions)
    bpy.ops.import_scene.gltf(filepath=path)
    new_objects = [o for o in bpy.data.objects if o not in before_objects]
    new_actions = [a for a in bpy.data.actions if a not in before_actions]
    for obj in new_objects:
        bpy.data.objects.remove(obj, do_unlink=True)
    return new_actions

viseme_actions = import_actions(lipsync_path)
blink_actions = import_actions(blink_path)
print("VISEME_ACTIONS:", sorted(a.name for a in viseme_actions))
print("BLINK_ACTIONS:", sorted(a.name for a in blink_actions))

if char_armature.animation_data is None:
    char_armature.animation_data_create()

def pose_and_bake(action, key_name):
    char_armature.animation_data.action = action
    frame = int(action.frame_range[1])
    bpy.context.scene.frame_set(frame)
    bpy.context.view_layer.update()
    for mesh in face_meshes:
        modifier = next(
            (m for m in mesh.modifiers if m.type == "ARMATURE"), None
        )
        if modifier is None:
            continue
        bpy.context.view_layer.objects.active = mesh
        bpy.ops.object.modifier_apply_as_shapekey(
            keep_modifier=True, modifier=modifier.name
        )
        keys = mesh.data.shape_keys.key_blocks
        keys[-1].name = key_name
    char_armature.animation_data.action = None

skip = {"stand_talk"}
baked = []
for action in sorted(viseme_actions, key=lambda a: a.name):
    if action.name in skip:
        continue
    name = "viseme_sil" if action.name == "sil" else f"viseme_{action.name}"
    pose_and_bake(action, name)
    baked.append(name)

blink = next((a for a in blink_actions if a.name == "blink_med"), None)
if blink is not None:
    pose_and_bake(blink, "blink")
    baked.append("blink")

# Back to rest so the export's base pose is the bind pose.
bpy.context.scene.frame_set(0)
print("BAKED_KEYS:", baked)
for mesh in face_meshes:
    if mesh.data.shape_keys:
        print(f"  {mesh.name}: {len(mesh.data.shape_keys.key_blocks) - 1} keys")

bpy.ops.export_scene.gltf(
    filepath=out_path,
    export_format="GLB",
    export_morph=True,
    export_apply=False,
    export_yup=True,
    export_draco_mesh_compression_enable=True,
    export_draco_mesh_compression_level=6,
    export_image_format="WEBP",
)
print(f"WROTE: {out_path}")
