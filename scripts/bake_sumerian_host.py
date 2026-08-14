"""Bake a Sumerian Host's bone-driven viseme poses into morph targets, and
its standing pose into the skeleton's REST pose.

Sumerian Host characters carry ZERO morph targets: their runtime animates
faces with facial bones, driven by pose clips in lipsync.glb whose names are
Polly's exact viseme codes (p, a, E, sil, ...). The LCDash avatar runtime is
morph-driven, so this bakes each viseme pose into a shape key named
``viseme_<code>`` (plus ``blink``), producing a GLB the runtime can drive
directly from Polly speech marks -- no approximation through ARKit names.

Body stance comes from stand_idle.glb's rest pose (arms at sides, not
T-pose). Face neutral comes from face_idle.glb, not from the lipsync set's
"sil" pose -- testing found "sil" is authored as a between-words talking
pose (lips faintly parted, ready to speak), not the actually-closed-mouth
resting face.

Face shape keys are built with Blender's "join as shapes" workflow, NOT
repeated ``modifier_apply_as_shapekey`` calls on one mesh. That repeated-
apply approach was tried first and its DEFAULT (all weights 0) state kept
rendering with an open mouth no matter what rest pose was baked in --
proven, by round-tripping the raw character through the exact same
export/import with zero baking involved, to be a bug in that specific
Blender operator's repeated use, not the rest pose, not Draco, not WebP.
"Join as shapes" poses a disposable duplicate mesh per action, freezes it
with a destructive (one-shot, not repeated) modifier apply, and joins its
geometry onto a clean frozen-neutral base as a new shape key -- the
standard, documented way to build shape keys from externally posed meshes.

    blender --background --factory-startup --python bake_sumerian_host.py --
        character.gltf stand_idle.glb face_idle.glb lipsync.glb blink.glb out.glb
"""
import sys

import bpy

argv = sys.argv[sys.argv.index("--") + 1:]
char_path, stand_idle_path, face_idle_path, lipsync_path, blink_path, out_path = argv

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

stand_idle_actions = import_actions(stand_idle_path)
face_idle_actions = import_actions(face_idle_path)
viseme_actions = import_actions(lipsync_path)
blink_actions = import_actions(blink_path)
print("STAND_IDLE_ACTIONS:", sorted(a.name for a in stand_idle_actions))
print("FACE_IDLE_ACTIONS:", sorted(a.name for a in face_idle_actions))
print("VISEME_ACTIONS:", sorted(a.name for a in viseme_actions))
print("BLINK_ACTIONS:", sorted(a.name for a in blink_actions))

if char_armature.animation_data is None:
    char_armature.animation_data_create()

# --------------------------------------------------------------------
# Bones whose REST POSE must not be touched.
#
# `pose.armature_apply` makes the current pose the new rest, and Blender
# warns outright: "Actions on this armature will be destroyed by this new
# rest pose as the transforms stored are relative to the old rest pose."
# That warning is load-bearing here. stand_idle's frame 0 does not only
# pose the body -- it also carries real offsets on 63 head-skinning bones
# (def_c_lowLip |loc|=0.0336, def_l_cornerLip 0.0174, def_c_upLip 0.0130,
# neckA 14.6 deg, neckB 5.3 deg). Folding those into the rest and THEN
# replaying a viseme action, whose channel values were authored against
# the ORIGINAL rest, composes idle-offset x viseme instead of just
# viseme. Measured against the un-rest-baked rig, that corrupted every
# single viseme by 65-309% of its own true displacement -- the error was
# larger than the signal, and it read as a crooked, one-sided mouth.
#
# So: neutralise every bone that a replayed action drives before applying
# any pose as rest. Those bones keep their authored rest, the actions stay
# valid, and the body still gets its standing stance from the bones the
# face never touches (arms, legs, fingers).
import re as _re

def _keyed_bones(action):
    found = set()
    for fcurve in action.fcurves:
        match = _re.match(r'pose\.bones\["([^"]+)"\]', fcurve.data_path)
        if match:
            found.add(match.group(1))
    return found

REST_LOCKED_BONES = set()
for _action in list(viseme_actions) + list(blink_actions):
    if _action.name == "stand_talk":
        continue
    REST_LOCKED_BONES |= _keyed_bones(_action)
print("REST_LOCKED_BONES:", len(REST_LOCKED_BONES))

def assign_action(obj, action):
    """Blender 4.4+'s layered-action system requires BOTH `.action` and
    `.action_slot` set, or the object is left completely undriven -- no
    error, no warning, just zero pose change. Confirmed directly: with only
    `.action` set, 0 of a viseme's 42 relevant face bones moved at any
    frame; explicitly assigning `action.slots[0]` immediately moved all 42,
    exactly the jaw/lip/chin/cheek bones a viseme should touch. Every
    "working" render before this fix was showing an unrelated bug's
    artifact, not real per-viseme deformation -- confirmed because ALL
    visemes were silently identical (all equally undriven)."""
    obj.animation_data.action = action
    if action.slots:
        obj.animation_data.action_slot = action.slots[0]

def bake_pose_into_rest(action):
    """Apply `action`'s frame-0 pose as the armature's new rest pose. Must
    run before any face freezing/duplication below: everything after this
    reads the CURRENT (armature-deformed) mesh as the neutral reference."""
    assign_action(char_armature, action)
    bpy.context.scene.frame_set(0)
    bpy.context.view_layer.update()
    locked = 0
    for pose_bone in char_armature.pose.bones:
        if pose_bone.name in REST_LOCKED_BONES:
            pose_bone.location = (0.0, 0.0, 0.0)
            pose_bone.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
            pose_bone.rotation_euler = (0.0, 0.0, 0.0)
            pose_bone.scale = (1.0, 1.0, 1.0)
            locked += 1
    print(f"  rest-locked {locked} action-driven bones before applying rest")
    bpy.context.view_layer.update()
    bpy.context.view_layer.objects.active = char_armature
    char_armature.select_set(True)
    bpy.ops.object.mode_set(mode="POSE")
    bpy.ops.pose.armature_apply(selected=False)
    bpy.ops.object.mode_set(mode="OBJECT")
    char_armature.animation_data.action = None

stand_idle = next((a for a in stand_idle_actions if a.name == "stand_idle"), None)
if stand_idle is not None:
    bake_pose_into_rest(stand_idle)
    print("STAND_IDLE_BAKED_AS_REST: yes")
else:
    print("STAND_IDLE_BAKED_AS_REST: NO STAND_IDLE ACTION FOUND")

face_idle = next((a for a in face_idle_actions if a.name == "face_idle"), None)
if face_idle is not None:
    bake_pose_into_rest(face_idle)
    print("FACE_IDLE_BAKED_AS_REST: yes")
else:
    print("FACE_IDLE_BAKED_AS_REST: NO FACE_IDLE ACTION FOUND")

# --------------------------------------------------------------------
# Duplicate the whole rig BEFORE freezing anything -- this duplicate is
# the disposable "pose source" used to generate each viseme's posed
# geometry. The original stays untouched until it is frozen (below) into
# the clean neutral base that every shape key gets joined onto.
# --------------------------------------------------------------------

def deep_copy(obj):
    new_obj = obj.copy()
    new_obj.data = obj.data.copy()
    bpy.context.collection.objects.link(new_obj)
    return new_obj

pose_armature = deep_copy(char_armature)
pose_armature.name = "PoseSourceArmature"
if pose_armature.animation_data is None:
    pose_armature.animation_data_create()

pose_face_meshes = {}
for mesh in face_meshes:
    dup = deep_copy(mesh)
    dup.name = mesh.name + "_pose_source"
    for modifier in dup.modifiers:
        if modifier.type == "ARMATURE":
            modifier.object = pose_armature
    # Armature deformation is driven entirely by the modifier's `.object`
    # target, independent of Blender's object parent/child hierarchy --
    # parenting exists here only as an organizational leftover from
    # obj.copy() (which preserves the ORIGINAL parent, char_armature, even
    # though the modifier above now points elsewhere). That stale parent
    # link is what corrupted export scale downstream, via a mesh that no
    # longer agrees with its own parent's transform once the modifier
    # target diverges from it. Dropping the parent removes the ambiguity
    # -- but MUST go through the operator, not `dup.parent = None`
    # directly: a bare attribute clear discards the parent's 0.01 scale
    # contribution outright rather than folding it into dup's own matrix
    # first, which reproduces the identical 100x error by a different
    # route (confirmed the hard way).
    bpy.context.view_layer.objects.active = dup
    bpy.ops.object.select_all(action="DESELECT")
    dup.select_set(True)
    bpy.ops.object.parent_clear(type="CLEAR_KEEP_TRANSFORM")
    pose_face_meshes[mesh.name] = dup

# Freeze the ORIGINAL face meshes at their current (correct, neutral)
# pose into plain static geometry: apply the Armature modifier
# DESTRUCTIVELY, once, per mesh -- not the repeated as-shapekey call that
# produced the bug. This becomes the Basis every shape key joins onto.
#
# Clear the parent link first (keeping world transform). These characters
# are authored in centimetres, carried as a 0.01 object-scale on the
# armature; `modifier_apply` on a mesh still parented to that scaled
# armature does not correctly fold the parent's scale into the baked
# result -- confirmed by bisection: this exact step, alone, with nothing
# else in the pipeline, exported a 174-unit-tall character (should be
# ~1.78). A parented mesh has no further use for the parent link once its
# deformation is frozen, so clearing it removes the ambiguity outright
# rather than compensating for it.
for mesh in face_meshes:
    modifier = next((m for m in mesh.modifiers if m.type == "ARMATURE"), None)
    if modifier is None:
        continue
    bpy.context.view_layer.objects.active = mesh
    bpy.ops.object.select_all(action="DESELECT")
    mesh.select_set(True)
    bpy.ops.object.parent_clear(type="CLEAR_KEEP_TRANSFORM")
    bpy.ops.object.modifier_apply(modifier=modifier.name)
    # These characters are authored in centimetres and rely on a 0.01
    # node-level scale to read correctly as metres. That is invisible to
    # any renderer while the mesh stays SKINNED (per the glTF spec, a
    # skinned mesh's vertex positions come from joint matrices, not its own
    # node transform, so the node's scale is moot) -- but this mesh just
    # lost its skin binding via the destructive apply above, making the
    # node's own scale suddenly load-bearing. Baking that scale directly
    # into the vertex data keeps the exported geometry correct without
    # depending on any downstream tool (including this project's own
    # validator, which deliberately ignores node transforms) correctly
    # applying node transforms for a static mesh.
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

def pose_and_join_as_shape(action, key_name):
    assign_action(pose_armature, action)
    # frame_range[1] is often fractional (glTF stores seconds; converting
    # to frames rarely lands on an integer) -- int() truncation was landing
    # BEFORE the pose fully settled. Setting the subframe directly targets
    # the exact end of the clip, matching what int() was trying to
    # approximate.
    end_frame = action.frame_range[1]
    bpy.context.scene.frame_set(int(end_frame), subframe=end_frame - int(end_frame))
    bpy.context.view_layer.update()
    for base_mesh in face_meshes:
        source = pose_face_meshes[base_mesh.name]
        temp = deep_copy(source)
        modifier = next((m for m in temp.modifiers if m.type == "ARMATURE"), None)
        if modifier is not None:
            bpy.context.view_layer.objects.active = temp
            bpy.ops.object.modifier_apply(modifier=modifier.name)
        # Bake this posed duplicate's 0.01 object scale into its vertices,
        # exactly as done to the target meshes above. join_shapes copies raw
        # vertex COORDINATES and ignores object transforms, so a
        # centimetre-scaled source joined onto a metre-scaled Basis writes
        # deltas 100x too large -- which rendered as the character's head
        # exploding into a black void the moment a viseme was applied.
        # Both sides of the join must agree on units.
        bpy.context.view_layer.objects.active = temp
        bpy.ops.object.select_all(action="DESELECT")
        temp.select_set(True)
        bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
        # join_shapes: select the shape SOURCE(S), make the TARGET active
        # last so it receives the new key. Vertex counts must match, which
        # they do -- temp is a duplicate of the same topology throughout.
        bpy.ops.object.select_all(action="DESELECT")
        temp.select_set(True)
        base_mesh.select_set(True)
        bpy.context.view_layer.objects.active = base_mesh
        bpy.ops.object.join_shapes()
        base_mesh.data.shape_keys.key_blocks[-1].name = key_name
        # Remove the OBJECT, not just its mesh data -- removing only
        # `.data` left a dataless Object behind every iteration (108 of
        # them across the full bake), unused but never cleaned up.
        temp_data = temp.data
        bpy.data.objects.remove(temp, do_unlink=True)
        bpy.data.meshes.remove(temp_data, do_unlink=True)
    pose_armature.animation_data.action = None

skip = {"stand_talk"}
baked = []
for action in sorted(viseme_actions, key=lambda a: a.name):
    if action.name in skip:
        continue
    name = "viseme_sil" if action.name == "sil" else f"viseme_{action.name}"
    pose_and_join_as_shape(action, name)
    baked.append(name)

blink = next((a for a in blink_actions if a.name == "blink_med"), None)
if blink is not None:
    pose_and_join_as_shape(blink, "blink")
    baked.append("blink")

bpy.data.objects.remove(pose_armature, do_unlink=True)
for dup in pose_face_meshes.values():
    bpy.data.objects.remove(dup, do_unlink=True)

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
