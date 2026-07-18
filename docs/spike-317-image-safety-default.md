# Spike #317 — Image-generation safety default posture

**Status:** resolved · **Date:** 2026-07-18 · **Blocks:** v0.58 (#330)
**Question:** what is the shipped default posture for the bundled image
generator's two safety dials (`CAPDEP_IMAGE_PROMPT_FILTER`,
`CAPDEP_IMAGE_SAFETY`), given that today they ship `off` and NSFW model
variants (`flux-nsfw`, `flux2-nsfw`, `sdxl-nsfw`, `pony-nsfw`) are wired?

## TL;DR decision

**Ship filters ON by default, posture-tiered.**

- **Default shipped state:** `CAPDEP_IMAGE_PROMPT_FILTER=on` **and**
  `CAPDEP_IMAGE_SAFETY=on`. A security product must not ship unsafe-by-default
  image generation.
- **`strict` and `high-security-useful`** force both dials **ON as
  non-negotiable** — a floor, exactly like the flow-pattern floor in
  `posture.py`. No config, custom posture, or subprocess env can turn them off.
- **`low-friction-practical`** still **defaults ON** but permits an **explicit,
  deliberate local opt-out** — a user config change, never the shipped default.
  This is the *only* posture where the NSFW / local-model research use case is
  reachable, and only as a deliberate operator act.
- **Unknown posture ⇒ fail-closed** (filters ON) — free, because
  `resolve_posture` already refuses an unknown id rather than defaulting.

The NSFW variants stay wired (they are local-model research assets), but they
are inert unless the operator both selects `low-friction-practical` *and*
explicitly opts the dials off — a two-step deliberate act.

## Three mechanisms — do not conflate them (evidence)

There are **three** distinct image-content mechanisms in the tree; only the last
two are the safety gate this spike governs. The first is routing, not a gate.

| Mechanism | Where | What it does | Gate dial |
|---|---|---|---|
| Intent/routing regexes | `agent/chat_turn.py` `_IMAGE_SCENE_RE` (57–66), `_IMAGE_GENERATION_INTENT_RE` (88–100) | Detect that the user *wants* an image and route the turn. **Deliberately permissive** — `IMAGE_GENERATION_RETRY_NOTICE` (127) literally says "generic adult portraits of women or men are allowed." | none — not a safety gate |
| **Prompt filter** | `_image_pipeline.py` `validate_prompt` (703–712) + `_FORBIDDEN_PROMPT_RE` (27–33) | Rejects a prompt when enabled; the regex blocks minor/child terms only (`minor|child|kid|teen|underage|loli|shota|pedoph…`). | **`CAPDEP_IMAGE_PROMPT_FILTER`** |
| **Output safety checker** | `_image_pipeline.py` generation path (910–917) | When `safety_enabled` is false, `pipe.safety_checker = None` + `pipe.feature_extractor = None` (uncensored). When true, the diffusers NSFW safety checker is retained on generated output. | **`CAPDEP_IMAGE_SAFETY`** |

The `chat_turn.py` regexes are intent detection for tool-call routing (they even
include a *refusal-suppression* notice); citing them as "the filter" would be a
category error. The safety gate is `validate_prompt` (input) +
`safety_checker` nulling (output).

## Current state — two unsafe default surfaces (evidence)

Both dials default **off**, and they do so on **two independent surfaces** — a
#330 fix must flip *both* or an absent env still lands unsafe:

1. **Code default** — `load_image_gen_config` reads
   `_truthy(os.environ.get("CAPDEP_IMAGE_SAFETY"), default=False)` and
   `_truthy(os.environ.get("CAPDEP_IMAGE_PROMPT_FILTER"), default=False)`
   (`_image_pipeline.py` 487–488). So when the env var is **absent**, the dial
   is off.
2. **Managed-config env blocks** — the shipped daemon config sets them off
   explicitly. `_managed_config.py`
   `BUNDLED_IMAGE_GENERATE_BLOCK_BODY` (543–574) ships
   `CAPDEP_IMAGE_PROMPT_FILTER: "off"` / `CAPDEP_IMAGE_SAFETY: "off"` (554–555),
   and the legacy combined `BUNDLED_IMAGES_BLOCK_BODY` (591–625) repeats it
   (602–603).

The NSFW variants are wired in the image pipeline's profile tables:
`_PROFILES` / `_PROFILE_METADATA` / `_PROFILE_ASSET_IDS` carry
`flux-nsfw` (135), `flux2-nsfw` (142), `sdxl-nsfw` (149), `pony-nsfw` (154) —
`diffusers`/`mflux` profiles whose asset ids map to local LoRA/checkpoint
adapters. The comment at 910–911 states the design intent bluntly: "Operator
opt-in only; default is uncensored local generation." **That default is exactly
what #317 reverses.**

## How the v0.56 posture presets work (evidence)

A **posture** (`policy/posture.py`) is a *named binding over existing dials* — it
invents no authority; the daemon still enforces. The three shipped presets
(`BUILTIN_POSTURES`, 126–176) — `strict`, `high-security-useful`,
`low-friction-practical` — "differ by DIAL and PATTERN-DEFAULT, never by floor."

The load-time floor mechanism is the pattern #330 should mirror. `Posture.validate()`
(99–110) refuses any posture that ratchets **below** the structural floor
(`_FLOOR_PATTERN`, 58–64) — "a posture may only ratchet STRICTER than the
structural floor, never below it." And `resolve_posture` (179–200) is
**fail-closed**: an unknown id **raises** `PostureError` rather than silently
defaulting, and a custom posture may not shadow a builtin preset id.

Two facts about the presets matter for #317:

- **`Posture` has no image-safety field today.** Its fields are
  `clearance_max_tier`, `risk_preference`, `flow_pattern_defaults`,
  `projection_only`, `inspector_set`, `retention` (80–92). Image safety is
  **not** modeled — #330 must add it.
- **The dials live in a different layer from posture.** The image dials are
  **subprocess env** in the managed-config block; the bundled image generator is
  a separate MCP subprocess that reads its own `CAPDEP_IMAGE_*` env at
  `load_image_gen_config` time. The posture policy lives in the daemon. Bridging
  the two layers is the core of the #330 work.
- **The opt-out is looser than the `projection_only` precedent.** No shipped
  preset sets `projection_only: false` — **not even `low-friction-practical`**
  (172; comment 160–161: "projection_only stays True even here"). The image
  decision is *analogous in shape* (a permissive-posture-only relaxation) but
  **strictly looser in fact**: `low-friction-practical` *is* granted an image
  opt-out, whereas it is denied the `projection_only` opt-out. Say this
  precisely — do not claim the two knobs behave identically.

## Rationale

A security product must not ship unsafe-by-default image generation. The two
dials today ship `off` on two surfaces and the pipeline comment calls the
default "uncensored local generation" — that is the wrong shipped posture for a
tool whose whole thesis is a fail-closed capable deputy. Flipping the default to
ON costs the research use case nothing that a deliberate operator act can't
restore, and only in the one posture that already advertises itself as the
low-friction tradeoff. Forcing ON in the two stricter postures makes the
guarantee non-negotiable where it must be, using the *same floor discipline*
(`validate()` / `_FLOOR_PATTERN`) the flow-pattern dial already uses — so the
#306 conformance harness can fuzz it as one more invariant. Fail-closed on an
unknown posture falls out of `resolve_posture` for free.

## Consequences for #330

#330 must implement, concretely:

1. **Flip the shipped defaults to ON — both surfaces.**
   - Code default: change `_image_pipeline.py` 487–488 `_truthy(..., default=False)`
     → `default=True` for both `CAPDEP_IMAGE_SAFETY` and
     `CAPDEP_IMAGE_PROMPT_FILTER`, so an **absent** env is safe.
   - Managed config: change `_managed_config.py` 554–555 (and the legacy
     603) from `"off"` → `"on"` in every image block.
2. **Add an image-safety field to `Posture`.** Extend `policy/posture.py`
   `Posture` (80–92) with an image-safety setting (e.g.
   `image_filters: Literal["forced_on", "default_on_optout_ok"]` or a small
   struct covering both dials).
3. **Enforce it with a floor analogous to `validate()`.** In the shipped
   `BUILTIN_POSTURES` (126–176): `strict` and `high-security-useful` →
   forced-on (non-negotiable); `low-friction-practical` → default-on with
   opt-out permitted. Add a `validate()`-style check so a *custom* posture
   cannot set the two strict presets' semantics below the floor (mirror
   `_FLOOR_PATTERN` / lines 99–110).
4. **Bridge the layer gap — inject posture-derived env at image-subprocess
   spawn.** The subprocess env is applied at spawn by the upstream supervisor:
   `upstream/supervisor.py` `build_stdio_env` does `env.update(config.env)`
   (95), consumed at the `subprocess`/`Popen` spawn (`env=build_stdio_env(...)`,
   318). #330 must derive the two `CAPDEP_IMAGE_*` values from the **active
   posture** and inject them here so they **override** the managed-config block —
   otherwise a stale on-disk `off` in the config would defeat a forced-on
   posture. For the two strict postures the injected values must win
   unconditionally; for `low-friction-practical` the injected value is `on`
   unless the operator's explicit local opt-out is present.
5. **Allow opt-out only in `low-friction-practical`.** The opt-out must be an
   explicit operator config change (never the shipped default), gated on the
   active posture being `low-friction-practical`. Under `strict` /
   `high-security-useful` the opt-out is ignored (forced-on floor).
6. **Fail-closed on unknown posture.** Reuse `resolve_posture` (179–200): an
   unknown posture id already raises `PostureError`. #330 must ensure the image
   env derivation runs *after* posture resolution, so a config that can't
   resolve a posture never reaches the "inject `off`" path — the pipeline stays
   filtered.

Leave the four `*-nsfw` profiles wired (they are legitimate local-model research
assets). Post-#330 they are reachable only via the deliberate
`low-friction-practical` + explicit-opt-out path; in the shipped default and in
the two strict postures they generate through the retained safety checker and
the prompt filter.
