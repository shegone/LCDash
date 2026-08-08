# MAE Visual Identity

MAE (Mission Assistance Engine) uses a synthetic virtual-assistant portrait.
The portrait is a visual identity for the software and does not represent a
Logan County 911 employee or any other real person.

## Selected primary portrait

`static/img/mae/mae-neutral.jpg` is the primary interface portrait.

It was selected because its straight-on pose, even lighting, neutral expression,
and relaxed closed mouth are the strongest foundation for future facial
animation and lip synchronization.

## Reference set

- `mae-neutral.jpg` - primary neutral pose
- `mae-soft-smile.jpg` - subtle friendly expression
- `mae-smile.jpg` - stronger smile
- `mae-warm-smile.jpg` - alternate warm expression
- `mae-three-quarter.jpg` - reference for future head turns

## Future animation path

The next avatar milestone should keep the current static portrait as a reliable
fallback while adding an optional animated layer:

1. Use MAE's locally generated speech audio as the animation input.
2. Generate mouth timing from phonemes or audio-driven visemes.
3. Add natural blinking and restrained idle head motion.
4. Use the three-quarter portrait to guide head-turn consistency.
5. Automatically return to the static portrait when animation is unavailable.

Animation must never delay or block MAE's text response, operational data, or
read-only safety controls.

## Dedicated MetaHuman workstation

The planned animation host is a separate Windows workstation named
`mae-avatar-01` with an Intel i9-12900KF, 128 GB RAM, an NVIDIA RTX 3090
24 GB GPU, and a 2 TB SSD.

- Preserve Windows and use the NVIDIA Studio driver branch.
- Run Unreal Engine, MetaHuman, facial animation, lip synchronization, and
  Pixel Streaming on `mae-avatar-01`.
- Keep `.227` as the production AI, audio, CAD, application, and database
  server; do not place Unreal or MetaHuman rendering workloads on it.
- Treat the animated avatar as an optional presentation layer. The static MAE
  portrait remains the mandatory fallback whenever the workstation, renderer,
  stream, or animation input is unavailable.
- Keep operational responses usable when the avatar is disabled or offline.

Before installing the avatar software stack, inventory Windows version and
activation, firmware, storage health and free space, NVIDIA driver and GPU
status, network interface and address plan, audio devices, display setup, and
remote-administration method.
