# FLOWCHARTS

Mermaid control-flow diagrams traced directly from the live source
(`agent.py`, `prepass.py`, `dedup.py`, `manifest.py`, `drive.py`,
`translation_engine.py`, `pdf_mode_detector.py`). Node labels carry the exact
function names so each diagram maps 1:1 to the code. Where the code differs from
the brief, the code wins and a note records the difference.

---

## 1. Run level — the whole agent run

End-to-end `agent.py __main__`: config → pre-pass → (maybe) the loop → the
always-run cost summary.

```mermaid
flowchart TD
    A["__main__ start"] --> B["courses.load_courses()"]
    B --> C["build mappings_block"]
    C --> D["open logs/agent_run_id.log<br/>set LEDGER_PATH ledger_run_id.jsonl"]
    D --> E["prepass.build_worklist(ROOT_FOLDER_ID)<br/>returns worklist, total_scanned"]
    E --> F{"worklist empty?"}
    F -->|"yes"| G["print 'Nothing new'<br/>log; skip loop"]
    F -->|"no"| H["build kickoff + messages"]
    G --> Z["finally: RUN SUMMARY"]
    H --> L{"while worklist<br/>(loop)"}
    L --> M["turn += 1; CURRENT_TURN = turn<br/>_set_cache_breakpoint(messages)"]
    M --> N["client.messages.create<br/>model=claude-opus-4-8, tools=TOOLS"]
    N --> O["costs.record_call('routing')<br/>log usage + cache r/w"]
    O --> P{"stop_reason?"}
    P -->|"end_turn"| Q["RUN COMPLETE<br/>break"]
    P -->|"tool_use"| R["append assistant turn"]
    R --> S{"for each tool_use block"}
    S --> T{"tool_calls &gt;= 200?"}
    T -->|"yes"| U["budget_hit = True<br/>break"]
    T -->|"no"| V["tool_calls += 1<br/>HANDLERS[name](input)"]
    V --> W["parse result → audit events<br/>(saved/refused/skipped/auto-named/cost)"]
    W --> S
    S -->|"blocks done"| X["append tool_results as user turn"]
    X --> Y{"budget_hit?"}
    Y -->|"yes"| Z
    Y -->|"no"| L
    Q --> Z
    U --> X
    L -.->|"any exception"| EX["except: log RUN CRASHED"]
    EX --> Z
    Z --> ZZ["print + log summary; close log"]
```

Note: the loop guard is literally `while worklist:` and `worklist` is never
mutated — iteration ends only via `break` (`end_turn` or budget) or an exception;
the empty-worklist case never enters the body. The 200-cap check happens *before*
each dispatch, so the run stops at exactly 200 real tool calls even within a
batched turn.

---

## 2. Pre-pass internal

`prepass.build_worklist` → `walk_tree` (recurse, md5 metadata only) → `diff_tree`
(pure per-file md5 decision). Absence from the worklist *is* the "unchanged"
signal.

```mermaid
flowchart TD
    A["build_worklist(root_folder_id)"] --> B["walk_tree(root, list_children)"]
    B --> C["drive.list_folder_children(fid, include_md5=True)<br/>NO downloads"]
    C --> D{"child type?"}
    D -->|"folder"| E["recurse into folder<br/>path + [name]"]
    E --> C
    D -->|"file"| F["collect {id, name, parent_path, md5}"]
    F --> C
    C -->|"tree walked"| G["entries = manifest.load_log()"]
    G --> H["diff_tree(files, entries)"]
    H --> I{"for each file f"}
    I --> J{"dedup.md5_gate hit?<br/>(not None)"}
    J -->|"yes"| K["EXCLUDE<br/>already translated, unchanged"]
    J -->|"no"| M{"dedup.skip_unchanged hit?"}
    M -->|"yes"| N["EXCLUDE<br/>deliberately skipped, unchanged"]
    M -->|"no"| O["APPEND to worklist<br/>{file_id, name, parent_path}"]
    K --> I
    N --> I
    O --> I
    I -->|"files done"| P["return (worklist, len(files))"]
```

---

## 3. Dedup decision (`dedup.py`, single source)

The three pure verdict functions. `md5_gate` + `skip_unchanged` run pre-download
(in `diff_tree`); `hash_dedup` runs post-download (in `read_file_logic`).

```mermaid
flowchart TD
    subgraph PRE["pre-download (md5 only)"]
        A["md5_gate(entries, file_id, drive_md5)"] --> B{"drive_md5 is None?<br/>(Google Doc)"}
        B -->|"yes"| C["N/A → None<br/>(gate did not fire)"]
        B -->|"no"| D{"entry has md_path<br/>AND source_md5 == drive_md5?"}
        D -->|"yes"| E["already_done (md_path)"]
        D -->|"no"| F["None → caller continues"]
        F --> G["skip_unchanged(entries, file_id, drive_md5)"]
        G --> H{"entry.model == skipped_permanent<br/>AND source_md5 == drive_md5?<br/>(drive_md5 not None)"}
        H -->|"yes"| I["True → EXCLUDE in diff_tree"]
        H -->|"no"| J["False → file stays in scope"]
    end
    subgraph POST["post-download (content SHA)"]
        K["hash_dedup(entries, file_id, source_hash)"] --> L{"branch b:<br/>entry.source_content_hash == source_hash?"}
        L -->|"md_path set"| M["already_done (md_path)"]
        L -->|"model skipped_permanent"| N["already_done (skip_reason)"]
        L -->|"not_translated_yet / no match"| O["fall through"]
        O --> P{"branch c: any OTHER entry<br/>md_path set AND same source_hash?"}
        P -->|"yes"| Q["already_done (md_path)"]
        P -->|"no"| R["PROCEED"]
    end
```

Note: `read_file_logic` calls only `md5_gate` then `hash_dedup`; it does **not**
call `skip_unchanged` (that one fires solely in the pre-pass `diff_tree`). The
diagram groups all three because `dedup.py` is the single decision source for both
sites.

---

## 4. Tool — `list_folder`

`handle_list_folder`: a thin pass-through to Drive; no status branches.

```mermaid
flowchart TD
    A["handle_list_folder(inp)"] --> B["drive.list_folder_children(folder_id)<br/>include_md5 defaults False"]
    B --> C["paginate via files().list(num_retries=5)"]
    C --> D["json.dumps(children) → JSON ARRAY<br/>[{name, id, type, mime_type}]"]
```

---

## 5. Tool — `read_file`

`handle_read_file` → `read_file_logic`: md5 gate → download+verify → hash → dedup
→ detector → cache writes → `ready`. Every failure returns `error`, never raises.

```mermaid
flowchart TD
    A["read_file_logic(file_id)"] --> B["drive.file_md5(file_id)"]
    B -->|"exception"| BE["return error<br/>'metadata fetch failed'"]
    B --> C["entries = manifest.load_log()"]
    C --> D["dedup.md5_gate(entries, file_id, drive_md5)"]
    D -->|"verdict not None"| DE["return already_done (md_path)"]
    D -->|"None"| E["drive.download_bytes(file_id)<br/>(size-verified, re-download on short read)"]
    E -->|"exception"| EE["return error<br/>'download failed'"]
    E --> F["source_hash = manifest.sha256_of(bytes)"]
    F --> G["dedup.hash_dedup(entries, file_id, source_hash)"]
    G -->|"status already_done"| GE["return already_done"]
    G -->|"PROCEED"| H["detect_pdf_mode(bytes)"]
    H -->|"exception"| HE["return error<br/>'detector failed'"]
    H --> I["CACHE: source_hash → bytes<br/>:md5, :signals (lean), :signals_full"]
    I --> J["return ready<br/>source_hash, page_count, file_size_kb,<br/>5 scalar signals, signals_full_handle"]
```

---

## 6. Tool — `translate_text_pdf`

`handle_translate_text` → `_translate_logic(engine.translate_text_pdf,
'translation_text')`. Two refusal paths (yield RuntimeError and `REFUSED:`
first-line) plus generic error.

```mermaid
flowchart TD
    A["_translate_logic(inp, translate_text_pdf)"] --> B{"source_hash in CONTENT_CACHE?"}
    B -->|"no"| BE["return error 'cache miss'"]
    B -->|"yes"| C["pdf_bytes = CACHE; today_date = utcnow"]
    C --> D["engine.translate_text_pdf(...)<br/>pypdf extract_text per page"]
    D --> E{"extracted len &lt; 50?"}
    E -->|"yes"| F["RuntimeError → return refused"]
    E -->|"no"| G["client.messages.create<br/>system=_text_system_prompt() (per call)"]
    G -->|"RuntimeError"| F
    G -->|"other Exception"| GE["return error 'translation failed'"]
    G --> H["markdown = response.content[0].text"]
    H --> I{"markdown lstrip startswith 'REFUSED:'?"}
    I -->|"yes"| J["return refused (reason, cost_data)<br/>NO cache write"]
    I -->|"no"| K["CACHE: source_hash:md = markdown<br/>source_hash:cost = cost_data"]
    K --> L["return translated<br/>md_cache_handle, chosen_mode, cost_data"]
```

---

## 7. Tool — `translate_image_pdf`

`handle_translate_image` → `_translate_logic(engine.translate_image_pdf,
'translation_image')`. Same wrapper as text; the engine rasterises at 200 DPI and
sends vision blocks. No `&lt;50`-char gate (that is text-only).

```mermaid
flowchart TD
    A["_translate_logic(inp, translate_image_pdf)"] --> B{"source_hash in CONTENT_CACHE?"}
    B -->|"no"| BE["return error 'cache miss'"]
    B -->|"yes"| C["pdf_bytes = CACHE; today_date = utcnow"]
    C --> D["engine.translate_image_pdf(...)<br/>convert_from_bytes(dpi=200)"]
    D --> E["build content: date-injected text block<br/>+ one base64 PNG block per page"]
    E --> F["client.messages.create<br/>system=_image_system_prompt() (per call)"]
    F -->|"RuntimeError"| G["return refused"]
    F -->|"other Exception"| GE["return error 'translation failed'"]
    F --> H["markdown = response.content[0].text"]
    H --> I{"markdown lstrip startswith 'REFUSED:'?"}
    I -->|"yes"| J["return refused (reason, cost_data)<br/>NO cache write"]
    I -->|"no"| K["CACHE: source_hash:md, source_hash:cost"]
    K --> L["return translated<br/>md_cache_handle, chosen_mode, cost_data"]
```

Note: in image mode the text-path RuntimeError (`<50` chars) cannot fire — that
guard lives only in `translate_text_pdf`. The `refused` branch via RuntimeError is
shown for parity with the shared wrapper, but in practice image-mode refusal
arrives through the `REFUSED:` first-line check.

---

## 8. Tool — `save_to_vault`

`handle_save_to_vault` reads markdown/cost/signals from cache, then
`engine.save_to_vault` writes the `.md` atomically *before* the manifest upsert.

```mermaid
flowchart TD
    A["handle_save_to_vault(inp)"] --> B{"md = CACHE.get(md_cache_handle)?"}
    B -->|"None"| BE["return error 'md cache miss'"]
    B -->|"found"| C{"cost_data = CACHE.get(hash:cost)?"}
    C -->|"None"| CE["return error 'cost cache miss'"]
    C -->|"found"| D["read CACHE hash:signals, hash:md5 (optional)"]
    D --> E["engine.save_to_vault(...)"]
    E --> F["vault_output_path(type or custom_subfolder)"]
    F -->|"unknown type + no custom_subfolder"| FV["ValueError → return error"]
    F -->|"ok"| G["mkdir; write .tmp; os.replace → atomic .md"]
    G --> H["build entry (+source_md5 / signals if present)"]
    H --> I["manifest.load_log → upsert_entry → save_log<br/>(atomic, sole writer)"]
    I --> J["return saved (md_path relative)"]
```

---

## 9. Tool — `update_mapping`

`handle_update_mapping`: persist an agent-assigned course name, logged loudly.

```mermaid
flowchart TD
    A["handle_update_mapping(inp)"] --> B["courses.update_mapping(hebrew, english)<br/>atomic write courses.json"]
    B --> C["print 'AUTO-NAMED: hebrew → english'"]
    C --> D["return mapped (hebrew_name, english_name)"]
```

---

## 10. Tool — `skip_file`

`handle_skip_file`: write a `skipped_permanent` manifest entry; attach
`source_md5` only when the read_file cache has it.

```mermaid
flowchart TD
    A["handle_skip_file(inp)"] --> B["source_md5 = CACHE.get(source_hash:md5)"]
    B --> C["build entry: model=skipped_permanent,<br/>md_path=null, skip_reason, zeros"]
    C --> D{"source_md5 is not None?"}
    D -->|"yes"| E["entry['source_md5'] = source_md5<br/>(enables pre-pass skip_unchanged)"]
    D -->|"no"| F["omit source_md5<br/>(falls to loop next run)"]
    E --> G["manifest.load_log → upsert_entry → save_log"]
    F --> G
    G --> H["return skipped_permanent<br/>(drive_file_id, skip_reason)"]
```

---

## 11. Tool — `fetch_signal_detail`

`handle_fetch_signal_detail`: return the offloaded verbose signals for a
read_file handle.

```mermaid
flowchart TD
    A["handle_fetch_signal_detail(inp)"] --> B["detail = CONTENT_CACHE.get(handle)<br/>(handle = source_hash:signals_full)"]
    B --> C{"detail is None?"}
    C -->|"yes"| D["return error 'no signal detail cached'"]
    C -->|"no"| E["return ok + per_page + unrecognized_sample"]
```
