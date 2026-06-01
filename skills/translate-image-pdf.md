Vision path. Your input is page images — one base64 image block per page, in page order — rendered from one PDF. Everything in skills/translate-shared.md applies; this file covers only what is specific to image input.

Rendering: pages are rasterized with pdf2image at 200 DPI. Do not assume or ask for higher — vision token cost scales steeply with resolution, and 200 DPI is enough to read handwritten Hebrew.

Vision preamble: the call must state explicitly that the input is handwritten/scanned page images and that you should transcribe and describe what you actually see. This overrides the text-leaning default posture: in vision mode the figure rule's "can see the figure" branch in skills/translate-shared.md is the one that applies, not the "cannot see" branch.

Where the shared rules bite hardest — reference, do not restate:
- Refuse-rather-than-reconstruct (skills/translate-shared.md → Unreadable Source Material): image mode is the path that actually faces illegible handwriting and degraded scans, so it is where fabrication is most tempting and most damaging — the Lecture 4 failure (a handwritten page fabricated instead of refused) is why this rule matters most here.
- Blank box / unfilled placeholder (skills/translate-shared.md): image mode is the path that literally sees the student-fill boxes and empty template fields, so the pull to complete them is strongest here — the same Lecture 4 lesson applies.

Call mechanics: max_tokens=16000, model Opus 4.8 (claude-opus-4-8); capture input/output token cost from response.usage.
