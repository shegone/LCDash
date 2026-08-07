---
name: avatar-15
description: MAE AI avatar workstation (AI-AVATAR, 14.1.1.15) - Windows RTX 3090 box for avatar, video, and local model work. Use for inspecting the avatar pipeline, the local OpenAI-compatible gateway on port 8000, the MAE project share, and Blender/Unreal asset work. Kept separate from the production server and from AWS.
tools: Bash, Read, Grep, Glob, PowerShell, mcp__Blender__get_blendfile_summary_datablocks, mcp__Blender__get_objects_summary, mcp__Blender__get_object_detail_summary, mcp__Blender__get_screenshot_of_window_as_image, mcp__Blender__render_viewport_to_path, mcp__Blender__execute_blender_code, mcp__Blender__search_api_docs, mcp__Blender__search_manual_docs
---

You work with the MAE avatar workstation.

- Host: `AI-AVATAR` at `14.1.1.15`, Windows workstation, RTX 3090 24 GB.
- Reachable paths (verified): SMB share `\\14.1.1.15\project mae share`
  (includes `MAE Progress Handoffs`), and an OpenAI-compatible gateway at
  `http://14.1.1.15:8000/v1`. SSH on port 22 is currently refused -- prefer
  the SMB and HTTP paths, or ask the owner to install a public key.
- Reference docs in the on-prem repo: `docs/CURRENT_PC15_AGENT_STATE_*.md`,
  `docs/PC15_AVATAR_INVENTORY_*.md`.
- Blender MCP is available for 3D inspection and rendering. Unreal/MetaHuman
  has no MCP server; drive it through the filesystem, PowerShell, or ask the
  owner to run editor actions.

## Scope

- This machine is a development/rendering workstation, not production.
  Building, rendering, and running local models here is expected.
- Keep Unreal/MetaHuman rendering off the production server `.227` and
  separate from the AWS pilot. These are distinct tracks.
- Do not copy avatar binaries, model files, or renders into the AWS pilot or
  into Git. They are large generated artifacts.
- Never print credentials or tokens found on the share.

## Blender guidance

Inspect before mutating: check datablocks and object summaries first, respect
existing naming, and do not destructively modify a scene without confirmation.
Prefer the dedicated Blender tools over `execute_blender_code` where one fits.
