Text-extraction path. Your input is pypdf-extracted text from one PDF — not page images. Everything in skills/translate-shared.md applies; this file covers only what is specific to extracted-text input.

Fragmented typed math (the main text-mode hazard):
pypdf splits embedded math fonts into separate Mathematical-Italic Unicode tokens — a typed `f(z)` arrives as `𝑓(𝑧)`, `jω` as `𝑗𝜔`, often with stray spacing and scattered subscripts. This is FRAGMENTED TYPED MATH, not OCR garbage. Reassemble it into proper LaTeX (`𝑓(𝑧)` → `$f(z)$`, `𝑗𝜔` → `$j\omega$`) using the LaTeX conventions in skills/translate-shared.md, and translate the surrounding prose faithfully.

Do not refuse over fragmentation:
The refuse-rather-than-reconstruct rule (skills/translate-shared.md → Unreadable Source Material) is for sources you genuinely cannot read — OCR garbage, illegible handwriting, blank templates. Fragmented typed math is READABLE: every character is present and the source was typed. Reassembling scattered math tokens into LaTeX is reconstruction of FORM, which is required here — it is not the forbidden reconstruction of unreadable CONTENT. When the underlying text decodes, reconstruct; do not mark it [OCR garbage].

For output format, YAML frontmatter, figure handling (text mode is always the "cannot see the figure" case in skills/translate-shared.md), glossary, and the refusal rules — follow skills/translate-shared.md. Not restated here.

Call mechanics: max_tokens=16000, model Opus 4.8 (claude-opus-4-8); capture input/output token cost from response.usage.
