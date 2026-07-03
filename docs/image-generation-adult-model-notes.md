# Image Generation Adult-Model Capability Notes

Status: deferred follow-up note from local MLX/MFLUX image-generation testing.

## Current Decision

Do not spend more time on Civitai downloads right now. Keep the local CapDep image
generation work focused on the MFLUX/MLX backend, named runtime profiles, and a
future SDXL/Pony fallback path that can consume locally downloaded checkpoints
when available.

## Hugging Face Access

The local Hugging Face token was verified to have access to the base models we
identified for the current image-generation stack:

- `black-forest-labs/FLUX.1-dev`
- `black-forest-labs/FLUX.1-schnell`
- `black-forest-labs/FLUX.2-klein-9B`
- `black-forest-labs/FLUX.2-klein-4B`
- `briaai/Fibo-lite`

This means no further Hugging Face agreement action is currently needed for
those base models. This does not imply access to every possible Hugging Face
image model, only the base models checked for this work.

## CapDep Runtime Support Added

CapDep now has image-generation profile support:

- `default`: fast MFLUX/MLX Z-Image-Turbo when available.
- `flux-nsfw`: MFLUX FLUX.1 profile intended for LoRA testing.
- `flux2-nsfw`: MFLUX FLUX.2 Klein 9B profile, slower but more directly aligned
  with newer Flux2 adult LoRAs.
- `sdxl-nsfw`: Diffusers SDXL fallback profile for mature adult checkpoints.
- `pony-nsfw`: Diffusers Pony/Illustrious fallback profile for the mature
  Pony/Illustrious adult ecosystem.

The fallback path can use either Hugging Face repo files or local `.safetensors`
checkpoint paths:

- `CAPDEP_IMAGE_CHECKPOINT_PATH`
- `CAPDEP_IMAGE_GRAPHIC_NOVEL_CHECKPOINT_PATH`

MFLUX profiles use:

- `CAPDEP_IMAGE_LORAS`
- `CAPDEP_IMAGE_LORA_SCALES`

Image generation is serialized in-process and across local processes so multiple
requests do not compete for Metal/GPU and unified memory.

## Local Flux LoRA Findings

The following local LoRA compatibility tests succeeded:

- `FLUX_NSFW.safetensors` with `FLUX.1-schnell`
  - MFLUX matched `912/912` LoRA keys.
  - Generated a 512 px image in about 22 seconds.
  - Adult/nude direction worked, but the probe produced implied adult content
    rather than reliably explicit visible sex-act anatomy.

- `Doggy_style_v3.safetensors` with `FLUX.1-schnell`
  - MFLUX matched `912/912` LoRA keys.
  - Generated a 512 px image in about 23 seconds.
  - Adult/nude direction worked, but the probe still produced mostly implied
    content at the tested 4-step 512 px setting.

- `FLUX2_KLEIN_UNLOCKED_V2.safetensors` with `FLUX.2-klein-9B`
  - MFLUX matched `224/224` LoRA keys.
  - Generated a 512 px image in about 185 seconds for 4 steps.
  - More directly aligned with adult prompts, but much slower than the Flux.1
    `schnell` profile.

Practical read: Flux LoRAs work in the MFLUX engine and are laptop-usable, but
the mature SDXL/Pony/Illustrious ecosystem is still likely the better fallback
for reliable explicit adult-image generation.

## Deferred Civitai Work

Civitai is useful as a source for adult image-generation LoRAs and checkpoints,
but several stronger explicit candidates require Civitai login, NSFW account
settings, age/account verification, or API-token based access. Do not block the
current work on this.

Confirmed Civitai downloads/access during this pass:

- `NSFW UNLOCKED [LORA] FLUX / FLUX2 KLEIN / Z_IMAGE`
- `FLUX NSFW (Pony-inspired)`
- `FLUX_NSFW.safetensors`
- `Doggy_style_v3.safetensors`

Still access-gated via anonymous download probe:

- `Pyro's BJ` for Flux2 Klein 9B
- `Pyro's Cowgirls` for Flux2 Klein 9B
- `FLUX Doggy style sex`
- `Anal Sex FLUX`
- `Fluxxxible - Sexual Nudes for Flux`
- `NSFW FLUX LoRA`

## Candidate Families For Later

When returning to this work, prioritize:

1. Flux2 Klein adult LoRAs if Civitai access is available.
2. Flux.1 LoRAs for faster laptop-friendly adult-scene generation.
3. SDXL/Pony/Illustrious checkpoints for the mature adult fallback path.

Candidate families identified from Civitai metadata:

- Flux2 Klein: `NSFW UNLOCKED`, `Pyro's BJ`, `Pyro's Cowgirls`,
  `Flux4Play_Klein9B`.
- Flux.1: `Fluxxxible - Sexual Nudes for Flux`, `FLUX Doggy style sex`,
  `Anal Sex FLUX`, `FLUX NSFW (Pony-inspired)`.
- SDXL/Pony/Illustrious: `Pony Diffusion V6 XL`, `Pony Realism`,
  `Pony Real Plus`, `WAI-NSFW-Illustrious` variants, `FallenLeaf - NSFW -
  XL-Pony-Ill`.

## Next Step When Resuming

If Civitai access is later available, configure a token-based download path,
download one or two of the gated Flux2 Klein LoRAs, and benchmark:

- load compatibility;
- generation time at 512 px and 768 px;
- prompt adherence;
- whether the output is implied adult content or explicit adult anatomy/action;
- whether quality justifies the runtime cost versus the SDXL/Pony fallback.
