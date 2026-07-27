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
