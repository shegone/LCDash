"""Convert a Character Creator FBX into the GLB the avatar runtime expects.

Run inside Blender on the workstation that has the export:

    blender --background --factory-startup \
        --python scripts/cc_fbx_to_arkit_glb.py -- IN.fbx OUT.glb

Why this exists. Character Creator exports the right blendshapes under
Reallusion's own names -- ``Eye_Blink_L``, not ``eyeBlinkLeft`` -- and the
avatar runtime drives morph targets BY NAME. So a CC export loads
perfectly, looks perfect, and never moves a muscle. Verified against a real
CC/iClone export on the avatar workstation 2026-08-11: 267 shape keys, none
of them ARKit-named.

Two things are preserved on purpose:

* **ARKit names get added** by renaming the CC equivalents, because that is
  the portable contract -- the browser runtime today, Audio2Face later.
* **CC's ``V_*`` viseme shapes are left untouched.** They do not collide
  with ARKit names, and they are strictly better raw material for lip sync:
  Reallusion authored them AS visemes (V_Explosive, V_Dental_Lip, V_Tight_O
  ...), which is exactly what Polly's speech marks emit. Keeping them means
  the runtime can be upgraded to drive them directly without re-exporting
  the character.

Scale and orientation are left alone: the same export measured 1.711 m tall
with its feet on the origin, which is already what the camera framings
assume. glTF export handles Blender's Z-up to glTF's Y-up conversion.
"""

import sys

import bpy


# CC/Reallusion shape key -> ARKit name. Only entries where the CC shape is
# genuinely the same expression; a guessed mapping would animate the wrong
# part of her face, which is worse than a missing shape the runtime skips.
#
# ARKit has single shapes where CC splits left/right (browInnerUp,
# mouthRollLower, mouthFunnel, mouthPucker). Those take CC's left side as
# the driver rather than inventing a blend -- honest approximation, and the
# asymmetry is invisible at the distances this is viewed from.
CC_TO_ARKIT = {
    # Jaw
    "Jaw_Open": "jawOpen",
    "Jaw_Forward": "jawForward",
    "Jaw_L": "jawLeft",
    "Jaw_R": "jawRight",
    # Mouth -- the ones lip sync actually drives
    "Mouth_Close": "mouthClose",
    "Mouth_Funnel_Up_L": "mouthFunnel",
    "Mouth_Pucker_Up_L": "mouthPucker",
    "Mouth_Smile_L": "mouthSmileLeft",
    "Mouth_Smile_R": "mouthSmileRight",
    "Mouth_Press_L": "mouthPressLeft",
    "Mouth_Press_R": "mouthPressRight",
    "Mouth_Roll_In_Lower_L": "mouthRollLower",
    "Mouth_Roll_In_Upper_L": "mouthRollUpper",
    "Mouth_Frown_L": "mouthFrownLeft",
    "Mouth_Frown_R": "mouthFrownRight",
    "Mouth_Dimple_L": "mouthDimpleLeft",
    "Mouth_Dimple_R": "mouthDimpleRight",
    "Mouth_Shrug_Lower": "mouthShrugLower",
    "Mouth_Shrug_Upper": "mouthShrugUpper",
    "Mouth_L": "mouthLeft",
    "Mouth_R": "mouthRight",
    "Mouth_Down_Lower_L": "mouthLowerDownLeft",
    "Mouth_Down_Lower_R": "mouthLowerDownRight",
    "Mouth_Pull_Upper_L": "mouthUpperUpLeft",
    "Mouth_Pull_Upper_R": "mouthUpperUpRight",
    # Eyes
    "Eye_Blink_L": "eyeBlinkLeft",
    "Eye_Blink_R": "eyeBlinkRight",
    "Eye_Squint_L": "eyeSquintLeft",
    "Eye_Squint_R": "eyeSquintRight",
    "Eye_Wide_L": "eyeWideLeft",
    "Eye_Wide_R": "eyeWideRight",
    "Eye_L_Look_L": "eyeLookOutLeft",
    "Eye_L_Look_R": "eyeLookInLeft",
    "Eye_L_Look_Up": "eyeLookUpLeft",
    "Eye_L_Look_Down": "eyeLookDownLeft",
    "Eye_R_Look_R": "eyeLookOutRight",
    "Eye_R_Look_L": "eyeLookInRight",
    "Eye_R_Look_Up": "eyeLookUpRight",
    "Eye_R_Look_Down": "eyeLookDownRight",
    # Brows
    "Brow_Raise_Inner_L": "browInnerUp",
    "Brow_Raise_Outer_L": "browOuterUpLeft",
    "Brow_Raise_Outer_R": "browOuterUpRight",
    "Brow_Drop_L": "browDownLeft",
    "Brow_Drop_R": "browDownRight",
    # Cheeks and nose
    "Cheek_Puff_L": "cheekPuff",
    "Cheek_Raise_L": "cheekSquintLeft",
    "Cheek_Raise_R": "cheekSquintRight",
    "Nose_Sneer_L": "noseSneerLeft",
    "Nose_Sneer_R": "noseSneerRight",
    # Tongue -- CC keeps this on the viseme set
    "V_Tongue_Out": "tongueOut",
}

# What the browser runtime drives today; missing any of these is fatal.
REQUIRED_NOW = (
    "jawOpen", "mouthClose", "mouthFunnel", "mouthPucker",
    "mouthSmileLeft", "mouthSmileRight", "mouthPressLeft", "mouthPressRight",
    "mouthRollLower", "tongueOut", "eyeBlinkLeft", "eyeBlinkRight",
    "browInnerUp",
)

TEXTURE_MAX_PX = 1024


def main() -> int:
    argv = sys.argv[sys.argv.index("--") + 1:]
    if len(argv) != 2:
        print("usage: ... --python cc_fbx_to_arkit_glb.py -- IN.fbx OUT.glb")
        return 1
    src, dst = argv

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.fbx(filepath=src)

    renamed = 0
    kept_visemes = 0
    seen_arkit = set()
    for obj in bpy.data.objects:
        if obj.type != "MESH" or not obj.data.shape_keys:
            continue
        for block in obj.data.shape_keys.key_blocks:
            arkit = CC_TO_ARKIT.get(block.name)
            if arkit:
                block.name = arkit
                seen_arkit.add(arkit)
                renamed += 1
            elif block.name.startswith("V_"):
                kept_visemes += 1

    # Drop every shape key we do not drive. A CC export carries ~267 of
    # them, each one a full set of per-vertex deltas, and that -- not the
    # textures -- is what made the first converted GLB 80 MB against a 30 MB
    # budget. Ears, eyelashes, pupil dilation and the rest are craft the
    # runtime never touches; keeping them would cost the booth laptop a
    # 50 MB download to animate nothing.
    wanted = set(CC_TO_ARKIT.values())
    dropped = 0
    for obj in bpy.data.objects:
        if obj.type != "MESH" or not obj.data.shape_keys:
            continue
        doomed = [
            block.name
            for block in obj.data.shape_keys.key_blocks[1:]
            if block.name not in wanted and not block.name.startswith("V_")
        ]
        for name in doomed:
            obj.shape_key_remove(obj.data.shape_keys.key_blocks[name])
            dropped += 1

    # Cap texture resolution. Character Creator ships 2K-4K maps per body
    # part, which is right for a cinematic render and wasted on a face
    # viewed at booth distance -- after pruning the morph targets, this is
    # what is left of the file size. 1024 keeps her likeness intact at the
    # sizes this is actually seen.
    resized = 0
    for image in bpy.data.images:
        width, height = image.size
        if max(width, height) > TEXTURE_MAX_PX:
            scale = TEXTURE_MAX_PX / max(width, height)
            image.scale(max(1, int(width * scale)), max(1, int(height * scale)))
            resized += 1

    # Repair transparency wiring. Blender's FBX importer wires a diffuse
    # texture's own alpha channel into Principled Alpha whenever the image
    # HAS one -- but Character Creator authors real transparency only in
    # dedicated *_Opacity maps. The head, eyeball and tongue diffuses carry
    # junk alpha channels (masks, not opacity), and honouring them dissolved
    # the face into black plates, emptied the eye sockets, and blacked out
    # the mouth -- rendered identically by Blender and three.js, so it was
    # the file, not the runtime. The rule, from dumping every material's
    # wiring on the real export:
    #   * alpha fed by an *_Opacity image  -> authored, keep as-is
    #     (hair, scalp, eyelash, shirt, slacks, heels, bra).
    #   * constant alpha < 1               -> authored, keep as-is
    #     (tearline 0.05, eye occlusion 0.0 -- meant to be invisible).
    #   * alpha fed by a Diffuse's channel -> importer artifact, sever it
    #     and force opaque (head, body eyes, tongue)...
    #   * ...EXCEPT the cornea, which really is a glass layer: it gets a
    #     constant low alpha instead of trusting its diffuse channel.
    severed = []
    for material in bpy.data.materials:
        if not material.use_nodes:
            continue
        tree = material.node_tree
        principled = next(
            (n for n in tree.nodes if n.type == "BSDF_PRINCIPLED"), None
        )
        if principled is None:
            continue
        alpha_input = principled.inputs.get("Alpha")
        if alpha_input is None or not alpha_input.is_linked:
            continue
        link = alpha_input.links[0]
        source = link.from_node
        image_name = (
            source.image.name if source.type == "TEX_IMAGE" and source.image else ""
        )
        if "Opacity" in image_name:
            continue  # authored transparency, leave it alone
        tree.links.remove(link)
        if "Cornea" in material.name:
            alpha_input.default_value = 0.1
        else:
            alpha_input.default_value = 1.0
            material.blend_method = "OPAQUE"
        severed.append(material.name)

    # Underwear never shows through clothing on a dressed character -- but
    # blended layers stacked inside blended layers z-fight, and the bra
    # rendered as dark blobs THROUGH the shirt. Delete hidden underlayers
    # outright; smaller file, zero sorting hazard.
    HIDDEN_UNDERLAYERS = ("Bra", "Underwear_Bottoms")
    removed_objects = []
    for obj in list(bpy.data.objects):
        if obj.type == "MESH" and obj.name in HIDDEN_UNDERLAYERS:
            removed_objects.append(obj.name)
            bpy.data.objects.remove(obj, do_unlink=True)

    # Cloth cutouts (open collar, shoe openings) are binary shapes, and
    # rendering them as gradient BLEND makes big meshes sort against
    # themselves -- dark plates on the shoulders and collar. Alpha-test
    # (CLIP -> glTF MASK) is artifact-free by construction. True gradients
    # (hair, eyelash, tearline, cornea) stay blended.
    CUTOUT_MATERIALS = ("shirt", "Slacks", "High_Heels", "Scalp")
    clipped = []
    for material in bpy.data.materials:
        if any(tag.lower() in material.name.lower() for tag in CUTOUT_MATERIALS):
            material.blend_method = "CLIP"
            if hasattr(material, "alpha_threshold"):
                material.alpha_threshold = 0.5
            clipped.append(material.name)

    # Cull backfaces everywhere (glTF doubleSided=false; three.js honours
    # it). The shirt and hair carry interior shells whose normals face
    # inward; rendered double-sided they appear as flat unlit plates on the
    # shoulders, collar and hair -- present in every version until a culled
    # test render removed them all. Front-facing avatar: nothing legitimate
    # is lost.
    for material in bpy.data.materials:
        material.use_backface_culling = True


    print(f"RENAMED_TO_ARKIT: {renamed}")
    print(f"KEPT_CC_VISEMES:  {kept_visemes}")
    print(f"DROPPED_UNUSED:   {dropped}")
    print(f"TEXTURES_RESIZED: {resized}")
    print("ALPHA_SEVERED:    " + (", ".join(severed) if severed else "none"))
    print("UNDERLAYERS_CUT:  " + (", ".join(removed_objects) if removed_objects else "none"))
    print("CUTOUT_CLIPPED:   " + (", ".join(clipped) if clipped else "none"))

    missing = [name for name in REQUIRED_NOW if name not in seen_arkit]
    if missing:
        # Loud, not fatal: a partial character is still worth eyeballing,
        # and the operator needs to know which CC shapes were absent.
        print("MISSING_REQUIRED: " + ", ".join(missing))
    else:
        print("MISSING_REQUIRED: none")

    bpy.ops.export_scene.gltf(
        filepath=dst,
        export_format="GLB",
        export_morph=True,
        export_apply=False,
        export_yup=True,
        # Draco squeezes mesh and morph data hard, and three.js decodes it
        # natively. The alternative -- decimating her -- costs likeness,
        # which is the one thing the character exists for.
        export_draco_mesh_compression_enable=True,
        export_draco_mesh_compression_level=6,
        # WEBP, never JPEG: JPEG cannot carry an alpha channel, and forcing
        # it strips the transparency the hair, lashes and cloth cutouts
        # need. AUTO keeps alpha but stores it as PNG, which weighed 46 MB;
        # WebP carries alpha at JPEG-like sizes, and three.js r171 reads
        # EXT_texture_webp natively.
        export_image_format="WEBP",
    )
    print(f"WROTE: {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
