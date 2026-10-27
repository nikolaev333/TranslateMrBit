# TranslateMrBit UI design system

## Typography

- Display: **Syne** (headings / brand)
- UI: **Manrope**
- Mono: **JetBrains Mono** (metrics, code-ish panels)

## Color tokens

| Token | Value | Usage |
|-------|-------|--------|
| `--bg` | `#080D20` | Page background |
| `--bg-2` | `#101A35` | Sidebar / secondary |
| `--bg-card` | `#151F3E` | Panels |
| `--cyan` | `#00C8F8` | Primary accent / focus |
| `--purple` | `#8539FF` | Secondary accent |
| `--pink` | `#E43CF5` | Gradient tip only |
| `--text` | `#F8FAFF` | Primary text |
| `--muted` | `#AAB8D6` | Secondary text |

## Components (CSS classes)

- App shell: `.app-shell`, `.app-sidebar`, `.app-header`
- `StatCard`: `.stat-card`
- `NeonButton`: `.btn.btn-primary`
- `UploadZone`: `.dropzone`
- `StatusIndicator`: `.badge.*`
- `DataTable`: `.data-table`
- `EmptyState` / skeletons: `.empty-state`, `.skeleton`

## Motion

Ambient glow + wave bars respect `prefers-reduced-motion`.
