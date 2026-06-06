"""Deterministic routing pre-pass (PHASE2_NOTES #11).

Runs BEFORE the agent loop. Diffs the Drive tree against the manifest by md5 —
cheap metadata, NO byte downloads, NO Opus, NO naming, NO skip-keyword logic —
and hands the loop a worklist of only NEW or CHANGED files. Unchanged files
(already translated, or deliberately skipped) drop out: absence from the worklist
IS the "unchanged" signal.

Layered so the decision logic is unit-testable without Drive/OAuth:
  - diff_tree(files, entries)        PURE — md5 diff via dedup.py, no I/O.
  - walk_tree(root, list_children)   recursion over an injected lister, no I/O of
                                     its own (the lister does the Drive calls).
  - build_worklist(root_folder_id)   wires the real drive lister + manifest.

The pre-pass is NAMING-BLIND and SKIP-BLIND: it never recognizes a course or a
"solution" file. A solved-homework file stays cheap because its bytes are
UNCHANGED (dedup), not because the pre-pass understands it. All semantic routing
(classify / translate / deliberate-skip) remains the agent loop's job.

md5-only by design: dedup.hash_dedup needs the content hash, which needs a
download — that would defeat the point for unchanged files. hash_dedup stays the
post-download authority INSIDE the loop (read_file_logic); the pre-pass uses the
md5 gates (md5_gate for translated, skip_unchanged for deliberate skips).
"""
import dedup
import drive
import manifest


def walk_tree(root_folder_id, list_children):
    """Recurse the Drive tree from root, collecting every FILE (folders are
    traversed, not emitted).

    list_children(folder_id) -> list of child dicts {id, name, type, md5Checksum}
    (type is "folder"/"file"). Injected so tests can pass a mock tree.

    Returns [{id, name, parent_path, md5}], where parent_path is the list of Hebrew
    folder names from the first level down to the file's immediate parent (the root
    folder itself is not included — the loop knows it's the semester root). The
    loop classifies course + type from parent_path + name."""
    files = []

    def rec(folder_id, path):
        for c in list_children(folder_id):
            if c["type"] == "folder":
                rec(c["id"], path + [c["name"]])
            else:
                files.append({
                    "id": c["id"],
                    "name": c["name"],
                    "parent_path": path,
                    "md5": c.get("md5Checksum"),
                })

    rec(root_folder_id, [])
    return files


def diff_tree(files, entries):
    """PURE md5 diff. Returns the worklist: the files that are NEW to the manifest
    or whose bytes CHANGED. A file is EXCLUDED (not in the worklist) iff the
    manifest already accounts for its current bytes — either translated and
    unchanged (dedup.md5_gate) or deliberately skipped and unchanged
    (dedup.skip_unchanged). Everything else (no entry, changed md5, or a
    not_translated_yet placeholder with no md5) goes in the worklist.

    Naming-blind, skip-blind, no I/O. `files` come from walk_tree; `entries` from
    manifest.load_log()."""
    worklist = []
    for f in files:
        fid, md5 = f["id"], f["md5"]
        if dedup.md5_gate(entries, fid, md5) is not None:
            continue   # already translated, bytes unchanged
        if dedup.skip_unchanged(entries, fid, md5):
            continue   # deliberately skipped, bytes unchanged
        worklist.append({
            "file_id": fid,
            "name": f["name"],
            "parent_path": f["parent_path"],
        })
    return worklist


def build_worklist(root_folder_id):
    """Wire the real Drive walk + manifest into diff_tree. Returns
    (worklist, total_files_scanned). The only function here that does I/O."""
    files = walk_tree(
        root_folder_id,
        lambda fid: drive.list_folder_children(fid, include_md5=True),
    )
    entries = manifest.load_log()
    return diff_tree(files, entries), len(files)
