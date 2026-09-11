# Session handoff: Nexis Call Flow Cards page

Date: 2026-09-10 (written 2026-09-11 from Git, tests, and the source files;
nothing here was taken from chat memory)
Scope: the `/callflow-cards` page in `E:\Projects\LCDash-AWS`, the September
6 through 10 build series that produced it, where its inputs live, what is
verified, what is not, and the one next action.

## How to use this document

Point a new thread at this path. Verify live AWS state before deploying or
changing anything; this snapshot records what is on disk and in Git, not
what is running at `aws.logan911.com` (see "Not verified" below).

## Repository state (verified 2026-09-11)

- Repository `E:\Projects\LCDash-AWS`, branch `main`, HEAD
  `5a29d11c88e168747fa46d90715327689e5e1a4f` (2026-09-10 16:37 EDT).
- Working tree clean. `main` is 0 commits ahead of its upstream, so HEAD
  is pushed. Remote `origin` fetches from `github.com/shegone/LCDash` and
  pushes to both that GitHub repo and
  `bitbucket.org/nga911rnd/dashboard`.
- No infrastructure, Dockerfile, or deploy-script change since `dd66953`
  (2026-09-06, pinned `python:3.13-alpine` base digest bump). Every commit
  after that is the cards page.

## What the page is

- Route `GET /callflow-cards` in `app/main.py` (around line 2699) returns
  `templates/nexis_callflow_cards.html` verbatim as a `FileResponse` with
  `Cache-Control: private, no-store`. It is deliberately not rendered
  through Jinja so the build's own template syntax cannot collide.
- Sidebar entry "Call Flow Cards" in `templates/layouts/base.html` (line
  102), inside the `{% if not restricted %}` block. Supervisor, dispatcher,
  and admin reach it; the restricted `user` and avatar tiers get 403.
- The template is one self-contained 890-line document: inline CSS, both
  card editions inlined as `window.NEXIS_DATA = {us:{cards},ph:{cards}}`,
  demo clips inlined as `window.NEXIS_MEDIA` data URIs, in-house SVG
  diagrams, a CPR metronome, search, and skill-card cross-links. No
  external calls except the Google Fonts stylesheet link in the head.
- It carries the ribbon "Prototype — not for operational use — pending
  agency authorization & medical review". Keep it until NGA911 authorizes
  operational use.

## Current build contents (read from the template at HEAD)

| Item | Value |
| --- | --- |
| `window.BUILD_STAMP` | `nexis-cfc-20260910` |
| US edition cards | 101 (EMS 45, FIRE 17, POLICE 31, DISASTER 8) |
| PH edition cards | 102 (EMS 46, FIRE 17, POLICE 31, DISASTER 8) |
| PH-only card added 9/10 | `EMS_Jellyfish-Marine-Sting.html`, "911 Dispatch Protocol: Jellyfish / Marine Sting" |
| Embedded clips | `tourn` (tourniquet), `pressure` (direct pressure), both labeled "AI-generated demonstration — pending medical-accuracy review" |
| Excluded clips | `aed` (take contradicts the card's pad placement), `birth` (non-instructional static take). Their slots show the in-house diagram with the video slot open. |

The 2026-09-10 commit changed exactly two lines of the template: the
`NEXIS_DATA` line and the `BUILD_STAMP` line. No JavaScript, CSS, route,
or test changed that day.

## Build series, September 6 to 10 (all on `main`)

- `4b56473` 9/6: page created, sidebar entry for operational roles.
- `6999a7f`, `40a48d2`, `6ab57fe` 9/7: tourniquet and direct-pressure
  diagrams, tourniquet clip, childbirth diagram, direct-pressure clip; AED
  clip held back.
- `b5f54e0`, `206b6f3`, `0203f84` 9/7: card group headings standardized
  (Level 1 / Level 2 / Pre-Arrival Instructions); non-instructional birth
  clip dropped; last retired-vocabulary label renamed (Sick Person prompt
  to "Patient Assessment").
- `9b28608`, `13e5775` 9/9: card-review batch in both editions (53
  redundant prompts removed, heading fixes, 71 cross-links including
  guide-card hand-offs rendered as CARD chips); 100 cards each.
- `32a75f6` 9/9: Choking Relief skill card added, 101 cards both
  editions; US drive-by card file renamed; build `nexis-cfc-20260909`.
- `5a29d11` 9/10: Jellyfish / Marine Sting guide card, PH only, 102 PH
  cards; build `nexis-cfc-20260910`.

## Where the build comes from (the part Git in this repo does not show)

The template is not authored here. It is the output of a packager in a
separate project, copied into `templates/` and committed.

- Packager: `package.py` in
  `C:\projects\nga911-ph-training-aws\.claude\worktrees\quirky-villani-0b2d1c\nexis-callflow-cards\`
  (branch `claude/quirky-villani-0b2d1c`, HEAD `3ab3958`, clean; not merged
  to that project's `master`, which sits at `7f53326`). It inlines both
  editions into `index.html` and writes `dist/Nexis-CallFlow-Cards.html`.
  Run with `python package.py`; the stamp is derived from the date.
- The `dist/` output is not tracked in that worktree. Its current file is
  byte-identical to this repo's template (MD5
  `b25a69010e032b9c900558a5196cfd5b` on both).
- Card masters are JSON on the Google Drive mount, read by absolute path
  inside `package.py`:
  `H:\My Drive\Documents NGA911 US Training\guidecards\protocol-cards-us.json`
  and `H:\My Drive\Documents NGA911 PH Training\guidecards\protocol-cards.json`.
  Demo clips are read from
  `H:\My Drive\Documents NGA911 US Training\image asset\` (`tourn.mp4`,
  `pressure.mp4`; `aed.mp4` and `birth.mp4` are commented out with reasons).
- Card content changes are made in `C:\projects\nga911-us-training`
  (`guidecards/`, `_tools/merge_cards_v5.py`). Its commit `dd0ee7c`
  (2026-09-10 16:51 EDT) merged the Jellyfish / Marine Sting card into the
  PH master and recorded it as deployed; SME review list section G was
  updated there.
- Packager warning that still applies: any file present at the `MEDIA`
  paths is embedded verbatim into every build. A new or replaced clip must
  be checked against the card's wording before the build is copied here.

Rebuild recipe, in order: edit masters in the US training project and
merge to the H: drive JSON, run `package.py`, confirm the printed card
counts, copy `dist/Nexis-CallFlow-Cards.html` over
`templates/nexis_callflow_cards.html`, run the tests below, commit with the
build stamp in the message.

## Verification run for this handoff (2026-09-11)

```
python -m pytest tests/test_callflow_cards.py tests/contracts/test_page_render_smoke.py -q
8 passed, 1 warning, 39 subtests passed in 3.18s
```

`tests/test_callflow_cards.py` proves: supervisor and dispatcher get 200
with the real build (title, `window.NEXIS_DATA`, and the pending-authorization
banner present), the `user` tier gets 403, and the sidebar links the page.
The render smoke test lists `/callflow-cards` in `SELF_CONTAINED_PAGES` so
the static-stylesheet checks that cannot apply to an inline-CSS page are
skipped. The full suite was not run for this handoff.

## Live deployment (verified 2026-09-11 via the AWS CLI)

- ECS cluster `lcdash-p1-logan-use1-cluster`, service
  `lcdash-p1-logan-use1-web`, task definition revision 97, deployment
  `PRIMARY` with rollout `COMPLETED`, 1 desired / 1 running.
- Image `sha256:bfb407a72f02f9f57e67e56687e7daf4d4b51500727b9d970a16de0022d990bd`,
  ECR tag `release-5a29d11c88e1`, pushed 2026-09-10 16:39 EDT, task
  definition registered 16:43, deployment updated 16:46.
- The tag is the short hash of `main` HEAD `5a29d11`, so the site serves
  build `nexis-cfc-20260910`. The two later commits (this handoff and its
  move into `handoffs/`) are documentation only and need no release.
- Earlier tags in the repository trace the build series:
  `release-206b6f34acde` and `release-0203f848fa15` (9/7),
  `release-32a75f6c0f9b` (9/10 12:32), `release-5a29d11c88e1` (9/10 16:39).

## Open risks and human gates (unchanged from the 9/7 record)

- NGA911 authorization for operational use of the page.
- Medical-accuracy review of every diagram and clip; the two embedded
  clips are AI-generated and labeled as pending review.
- Media-source decision (AI-generated, licensed, or filmed). A corrected AED
  take and an instructional birth take are still owed; dropping either in
  is a data change in `package.py`'s `MEDIA` map plus a rebuild, not code.
- The packager and its worktree branch are unmerged in the PH training
  project. If that worktree is pruned, the packager source is lost from the
  checkout (it remains in that repo's branch ref). Merging or at least
  pushing `claude/quirky-villani-0b2d1c` removes the risk.
- The packager reads the H: drive by absolute path, so builds only work on
  a workstation with that Google Drive mount.

## Exact next action

Nothing is owed on the deployment side. The next card change follows the
rebuild recipe above and then the guarded release path used for every
`release-<hash12>` tag (release-builder CDK asset, CodeBuild, ECR scan at 0
findings, named CloudFormation change set reviewed before execution, ECS
rollout and ALB health check). The open human gates below are what block
operational use.

## Codex catch-up

The Nexis Call Flow Cards page (`/callflow-cards`, served verbatim from
`templates/nexis_callflow_cards.html`) reached build `nexis-cfc-20260910`
at `main` HEAD `5a29d11` on 2026-09-10: US 101 cards, PH 102 cards after a
PH-only Jellyfish / Marine Sting card, two AI-generated clips embedded and
labeled pending medical review, AED and birth clips excluded. The template
is generated by `package.py` in an unmerged worktree of
`C:\projects\nga911-ph-training-aws` from JSON masters on the H: Google
Drive mount, and card edits happen in `C:\projects\nga911-us-training`.
The page tests pass (8 tests, 39 subtests). The tree is clean and pushed.
The live pilot runs this build: ECS revision 97, image tag
`release-5a29d11c88e1`, verified 2026-09-11. Operational-use authorization, medical review, and the media-source
decision remain open human gates.
