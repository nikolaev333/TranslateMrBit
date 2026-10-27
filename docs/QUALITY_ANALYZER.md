# MrBit Quality Analyzer

Original TranslateMrBit module for heuristic transcription diagnostics and human review.

Whisper remains a third-party speech engine. Quality rules, scoring, review workflow, and UI are TranslateMrBit application code.

## Important limitations

- Warnings are **heuristic indicators**, not proof of errors.
- Whisper metadata is **never** converted into an accuracy percentage.
- WER/CER require a **human reference** transcript — never fabricated.
- Silence intervals are informational, not automatic errors.
- Optional PDF export is **not** implemented; JSON reports are supported.

## Pipeline

```
Completed job
  → Queue quality_analysis_runs (async)
  → Segment + text rules (repetition, timestamps, language)
  → Optional Whisper JSON metadata heuristics
  → Optional FFmpeg/WAV audio diagnostics (background)
  → Optional glossary matches (Smart Vocabulary if present)
  → Review priority scoring
  → Persist warnings + human review actions
```

## Package layout

`app/services/quality_analyzer/`

- `text_metrics.py` — Unicode repetition detection
- `timestamp_analysis.py` — real timing anomalies only
- `audio_metrics.py` — silence / low volume / clipping heuristics
- `language_analysis.py` — script-mix heuristics
- `rules.py` — independent rule functions
- `risk_scoring.py` — explainable LOW/MEDIUM/HIGH/INSUFFICIENT_EVIDENCE
- `analyzer.py` — orchestration
- `schemas.py` — dataclasses + default rule config

## Database (additive)

- `quality_analysis_runs`
- `quality_warnings`
- `quality_reviews`
- `quality_reference_evaluations`

## API

| Method | Path |
|--------|------|
| POST | `/api/jobs/{id}/quality/analyze` |
| GET | `/api/jobs/{id}/quality` |
| GET | `/api/jobs/{id}/quality/warnings` |
| PATCH | `/api/jobs/{id}/quality/warnings/{wid}` |
| GET | `/api/jobs/{id}/quality/report` |
| POST | `/api/jobs/{id}/quality/evaluate` |

UI: `/quality/{job_id}`

## Environment

```
QUALITY_AUTO_ANALYZE=false
QUALITY_INCLUDE_AUDIO=true
QUALITY_MAX_AUDIO_ANALYZE_SECONDS=600
```

## Local testing

```bash
pytest tests/test_quality_analyzer.py -q
pytest -q
```

Synthetic fixtures only (`SampleAudio`, `SampleTranscript`, `ExampleUser`).

## Coolify (manual approval)

1. Deploy `feature/quality-analyzer` after review.
2. Keep `/data` and `/models` volume paths unchanged.
3. Confirm `/health` stays lightweight (analyzer does not load torch in API).
4. Quality worker runs inside the API process as a non-torch background poller.
