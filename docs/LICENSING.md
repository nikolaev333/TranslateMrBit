# Licensing — TranslateMrBit

## Product identity

| Item | Value |
|------|--------|
| Product name | **TranslateMrBit** |
| Tagline | Private AI Transcription Platform |
| GitHub repository | `nikolaev333/TranslateMrBit` |

Do not rename the product to other brand names in UI, docs, or packaging.

## Application vs model

- **TranslateMrBit** is the commercial application (UI, API, queue, storage, auth, branding).
- **OpenAI Whisper** (`openai-whisper`) may be used as an **internal** speech-to-text engine under its **MIT** license.
- TranslateMrBit does **not** own Whisper and must not present Whisper as a proprietary TranslateMrBit model.

## Required notices

- Keep [`NOTICE.md`](../NOTICE.md) in the repository and include its Whisper copyright/permission text in distributions that ship the Whisper dependency.
- Preserve MIT copyright and permission notices when redistributing Whisper-related code or binaries as required by the MIT license.

## Commercial edition notes

Commercial features (licensing gates, billing, multi-tenant controls, etc.) are added incrementally on branch `feature/commercial-edition` without changing the TranslateMrBit product name or claiming Whisper ownership.
