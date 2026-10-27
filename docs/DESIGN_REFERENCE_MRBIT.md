# Design reference — Casino MrBit visual cues (public observation)

**Product:** TranslateMrBit (independent AI transcription platform)  
**Purpose:** Inform an *original* UI inspired by publicly visible Casino MrBit aesthetics.  
**Disclaimer:** This does not claim affiliation, endorsement, or ownership of the MrBit trademark. No official logo files, game thumbnails, or promotional banners were downloaded or redistributed.

## Public references reviewed

| Source | Notes |
|--------|--------|
| https://www.mrbit.bg | Public marketing landing for online casino |
| https://mrbit.ro | Public RO market landing |

Observation method: public page presentation only. Source HTML/CSS was **not** scraped for reproduction.

## Observed stylistic cues (high level)

- Dark, high-contrast night-mode surfaces.
- Saturated accent lighting (cool cyan / electric blue vs magenta–violet).
- Rounded interactive cards and pill-like CTAs.
- Strong brand-first hero treatment on marketing surfaces.
- Dense dashboard-like casino lobbies → for TranslateMrBit we adapt this into a calmer **enterprise SaaS shell** with fewer neon layers.
- Neon used as accent, not as full-page flood lighting.

## TranslateMrBit design decisions

| Cue from references | Our independent choice |
|---------------------|------------------------|
| Dark lobby backgrounds | Deep navy `#080D20` / `#101A35` |
| Cool neon highlights | Electric cyan `#00C8F8` |
| Magenta / purple energy | Neon purple `#8539FF`, restrained pink `#E43CF5` |
| Card / panel chrome | Glass-like panels `#151F3E` with 1px cyan/purple border glow |
| Marketing hero | Original SVG waveform / neural geometry (no casino games) |
| Dense casino nav | Collapsible sidebar for product features only |

## Accessibility adjustments

- Body text on navy uses `#F8FAFF` / `#AAB8D6` for WCAG-oriented contrast.
- Neon reserved for focus, active nav, CTAs — not long-form text.
- `prefers-reduced-motion` disables ambient glow drift and waveform loops.

## What we explicitly did **not** copy

- Official MrBit wordmark / logo files.
- Slot / game artwork or promotional banners.
- Casino bonus copy, odds tables, or gambling UX patterns.
- Site source structure or class names.
