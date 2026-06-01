save_to_vault tool. This is the executor that writes the translated .md into the Obsidian vault and records it in the manifest. It does no translation; for the filename convention and output format, follow skills/translate-shared.md — not restated here.

Path — execute the agent's decision, don't re-decide:
The routing agent already classified course and type for this file during routing, using the full filename and content context. save_to_vault EXECUTES that decision — it does not re-derive where the file goes. Pass the type and course the agent already chose.

- Standard types (lecture / tutorial / homework / exam): vault_output_path maps type → folder through TYPE_TO_FOLDER. The map is fixed on purpose — one canonical folder name per type ("Lectures/" always, never a Lectures/ + lectures/ + Lecture/ split) so the ~60 files a semester don't fragment into casing-variant folders in Obsidian.
- Escape hatch — custom_subfolder: for a file that genuinely fits none of the four buckets (a formula/equation sheet, a syllabus, a general reference), the agent calls vault_output_path(..., custom_subfolder="<name>"), and the name is used verbatim as the subfolder. Use it ONLY for truly non-standard files — never to rename or alias a standard folder. Keep custom names consistent across runs: reuse an existing custom folder rather than minting a near-duplicate ("Formula Sheets" vs "Formulas" vs "formula_sheets").
- An unknown type with no custom_subfolder raises ValueError. That is intended: a missing or garbled type is a real error, not license to guess a folder.

Atomic write: write the .md file FIRST. Only if that write succeeds, update translated_log.json. If the disk write fails, the manifest stays clean — no record of a file that isn't on disk.

Manifest upsert: match the existing entry by drive_file_id and update it in place; never append a duplicate (verified L4 behavior). Record model="opus-4-8", the real cost from response.usage, and a vault-RELATIVE md_path (so records survive the vault root moving).

Open every file with explicit UTF-8 encoding (Hebrew appears in both filenames and content).
