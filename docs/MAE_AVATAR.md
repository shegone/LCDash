# MAE Visual Identity

> **Animation direction has moved.** The avatar build plan is
> [`docs/planning/MAE_AVATAR_PLAN_2026-08-09.md`](planning/MAE_AVATAR_PLAN_2026-08-09.md):
> a Character Creator 5 character rendered client-side in three.js, driven by
> Polly neural visemes first and Audio2Face-3D on `.15` later. The earlier
> direction this file described — MetaHuman on a dedicated `mae-avatar-01`
> workstation with Unreal Pixel Streaming — is superseded: MetaHuman Creator
> shuts down 2026-11-05, and server-side pixel streaming is priced out
> (~$734/mo per concurrent stream). This file now covers only MAE's visual
> identity and the static-portrait fallback, both of which remain true.

MAE (Mission Assistance Engine) uses a synthetic virtual-assistant portrait.
The portrait is a visual identity for the software and does not represent a
Logan County 911 employee or any other real person.

## Selected primary portrait

`static/img/mae/mae-neutral.jpg` is the primary interface portrait.

It was selected because its straight-on pose, even lighting, neutral expression,
and relaxed closed mouth are the strongest foundation for facial animation and
lip synchronization. The Character Creator 5 character being built for the
avatar (Phase 0 of the plan) should read as the same person as this portrait.

## Reference set

- `mae-neutral.jpg` - primary neutral pose
- `mae-soft-smile.jpg` - subtle friendly expression
- `mae-smile.jpg` - stronger smile
- `mae-warm-smile.jpg` - alternate warm expression
- `mae-three-quarter.jpg` - reference for head-turn consistency

## Standing rules (carried into the new plan)

- The animated avatar is an optional presentation layer. The static MAE
  portrait remains the mandatory fallback whenever the renderer, audio, or
  animation input is unavailable.
- Animation must never delay or block MAE's text response, operational data,
  or read-only safety controls.
- Keep `.227` as the production AI, audio, CAD, application, and database
  server; rendering workloads belong on the client or on `.15`.
