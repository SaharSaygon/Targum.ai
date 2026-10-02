#!/usr/bin/env python3
"""Menu-bar app for the Targum unattended runs (rumps; kept alive by launchd
ai.targum.menubar).

Read-only view over the wrapper's own artefacts - nothing here touches the
agent:
  logs/launchd.log   wrapper decision trail ("not due", "due → starting",
                     "run <id> finished: <STATUS>", abort lines)
  logs/last_run      epoch stamp of the last run that reached RUN SUMMARY
  logs/.run_marker   exists while a run is in progress
  logs/agent_sdk_<id>.log / agent_<id>.log   RUN SUMMARY block
  config.json        run_interval_days, max_files_per_run, root_folder_id,
                     model (the only keys this app ever writes; the
                     agent/wrapper re-read them at each firing)
  Drive (read-only)  folder names for the "Drive folder" chooser, via
                     core.drive (token.json)
  Drive for desktop  ~/Library/CloudStorage/GoogleDrive-*: the "Choose folder
                     in Finder" picker reads the folder's Drive id from its
                     com.google.drivefs.item-id#S xattr
  logs/.menubar_recent.json   recently chosen Drive folders (this app's own)
  scripts/ai.targum.agent.plist   daily firing hour (StartCalendarInterval)
  assets/logo/targum-menubar-icon.png   status-item icon (template image; the
                     state emoji is appended as the title)

"Run now" execs scripts/run_agent.sh --force (due-check bypassed; launchd's
own firings are unaffected). Refreshes when launchd.log's mtime changes,
checked every 60s.

    python scripts/menubar.py          # the app
    python scripts/menubar.py --print  # dump what the menu would show (no GUI)
"""
import datetime as dt
import json
import os
import pathlib
import plistlib
import re
import signal
import subprocess
import sys
import threading
import time
import traceback

ROOT = pathlib.Path(__file__).resolve().parent.parent
LOGS = ROOT / "logs"
WRAPPER_LOG = LOGS / "launchd.log"
STAMP = LOGS / "last_run"
MARKER = LOGS / ".run_marker"
KILLED_FLAG = LOGS / ".killed"   # read (and removed) by run_agent.sh
CONFIG = ROOT / "config.json"
WRAPPER = ROOT / "scripts" / "run_agent.sh"
AGENT_PLIST = ROOT / "scripts" / "ai.targum.agent.plist"
RECENT = LOGS / ".menubar_recent.json"
CLOUD_STORAGE = pathlib.Path.home() / "Library" / "CloudStorage"
DRIVEFS_ID_XATTR = "com.google.drivefs.item-id#S"

INTERVALS = (1, 2, 3, 5, 7, 14)
MAX_FILES_CHOICES = (0, 1, 3, 5, 10, 20, 50)   # 0 = unlimited
MODELS = (   # (model id, menu label); a config value outside this list is shown too
    ("claude-opus-5-5", "Opus 5.5"),
    ("claude-opus-5", "Opus 5"),
    ("claude-sonnet-5", "Sonnet 5"),
    ("claude-fable-5-1", "Fable 5.1"),
    ("claude-haiku-4-5-20251001", "Haiku 4.5"),
)
DEFAULT_MODEL = "claude-opus-5"   # core/config.py default when the key is absent
MAX_RECENT = 8
ICON = ROOT / "assets" / "logo" / "targum-menubar-icon.png"   # template image (black + alpha): macOS tints it
BASE_TITLE = "📚"   # text fallback, only used when the icon file is missing
STATE_EMOJI = {
    "running": "🔄",   # .run_marker present and the run_agent.sh shell alive
    "failed": "❌",    # last run CRASHED / aborted / interrupted
    "degraded": "⚠️",  # last run finished DEGRADED (transient errors)
    "due": "⏳",       # last run OK, next run due - waiting for the next firing
    "ok": "✅",        # last run OK, not due yet
}
# Only these states put an emoji next to the logo; everything else is the icon
# alone (the full STATE_EMOJI table still feeds the menu / --print text).
SHOWN_STATES = ("failed", "degraded")



def _base_title():
    """Text that precedes the state emoji in the status item: nothing when the
    logo icon is shown, the old book emoji when the icon file is missing."""
    return "" if ICON.exists() else BASE_TITLE


def status_title(state):
    """Status-item title for a state: the emoji only when something is wrong
    with the run (SHOWN_STATES), else None so the logo stands alone. Falls back
    to the book emoji when the icon file is missing."""
    emoji = STATE_EMOJI[state] if state in SHOWN_STATES else ""
    return (_base_title() + emoji) or None


_WRAPPER_LINE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) wrapper: (.*)$")
_FINISHED = re.compile(r"^run (\S+) finished: (.*)$")
_ABORTS = {
    "CONFIG ERROR": "CONFIG ERROR",
    "OAUTH TOKEN MISSING": "TOKEN MISSING",
    "NO NETWORK": "NO NETWORK",
}
_STARTING = ("due → starting", "forced run (--force)")


# ── config ──────────────────────────────────────────────────────────────────
def read_config():
    """Raw config.json dict (no validation - the app must not die on a typo)."""
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def write_key(key, value):
    """Rewrite ONLY `key` (int or str value) in config.json, preserving every
    other byte of the file (regex on the raw text, atomic replace)."""
    enc = json.dumps(value, ensure_ascii=False)
    text = CONFIG.read_text(encoding="utf-8")
    # existing value: a JSON number or a JSON string (no escaped quotes needed
    # for Drive ids / day counts)
    pat = re.compile(r'("%s"\s*:\s*)(-?\d+|"[^"\\]*")' % re.escape(key))
    if pat.search(text):
        new = pat.sub(lambda m: m.group(1) + enc, text, count=1)
    else:
        # key absent: append it inside the top-level object
        new = re.sub(r"\s*}\s*$", f',\n  "{key}": {enc}\n}}\n', text, count=1)
    before = json.loads(text)
    after = json.loads(new)
    before.pop(key, None)
    after.pop(key, None)
    if before != after:
        raise RuntimeError("refusing to write: other config keys would change")
    tmp = CONFIG.with_name(CONFIG.name + ".tmp")
    tmp.write_text(new, encoding="utf-8")
    os.replace(tmp, CONFIG)


def write_interval(days):
    days = int(days)
    if days < 1:
        raise ValueError("run_interval_days must be >= 1")
    write_key("run_interval_days", days)


def write_max_files(n):
    """0 = unlimited (the wrapper then passes no --limit)."""
    n = int(n)
    if n < 0:
        raise ValueError("max_files_per_run must be >= 0")
    write_key("max_files_per_run", n)


_MODEL_ID = re.compile(r"[a-z0-9][a-z0-9.\-]{2,80}")


def write_model(model):
    """The Claude model id every session of the next run uses (config model)."""
    model = (model or "").strip()
    if not _MODEL_ID.fullmatch(model):
        raise ValueError(f"not a model id: {model!r}")
    write_key("model", model)


def model_label(model):
    return next((f"{lbl} ({m})" for m, lbl in MODELS if m == model), model)


def write_root_folder(folder_id):
    """The Drive folder the agent scans (config root_folder_id). Takes effect on
    the next run; the manifest (translated_log.json) is keyed by file id, so
    files already translated stay recorded."""
    if not _DRIVE_ID.fullmatch(folder_id or ""):
        raise ValueError(f"not a Drive folder id: {folder_id!r}")
    write_key("root_folder_id", folder_id)


# ── Drive folder chooser (read-only, via the project's own Drive client) ────
_DRIVE_ID = re.compile(r"[A-Za-z0-9_-]{10,}")


def parse_drive_folder(text):
    """Folder id from a pasted Drive URL (.../folders/<id>?...) or a bare id."""
    text = (text or "").strip()
    m = re.search(r"/folders/([A-Za-z0-9_-]+)", text)
    if m:
        return m.group(1)
    return text if _DRIVE_ID.fullmatch(text) else None


def _drive_service():
    if not (ROOT / "token.json").exists():
        raise RuntimeError("no token.json - run the agent once to authorise Drive")
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from core import drive
    return drive, drive.get_service()


def _folders_in(drive, svc, folder_id):
    q = f"'{folder_id}' in parents and mimeType = '{drive.FOLDER_MIME}' and trashed = false"
    out, token = [], None
    while True:
        resp = svc.files().list(q=q, fields="nextPageToken, files(id, name)", pageSize=100,
                                orderBy="name", pageToken=token).execute(num_retries=2)
        out += [{"id": f["id"], "name": f["name"]} for f in resp.get("files", [])]
        token = resp.get("nextPageToken")
        if not token:
            return out


def drive_listing(root_id):
    """Current root folder, its parent, sibling folders and subfolders:
    {root, parent, siblings, children} (parent None at the top of My Drive).
    Three or four metadata calls; raises on any Drive/network failure."""
    drive, svc = _drive_service()
    root = svc.files().get(fileId=root_id, fields="id, name, parents, mimeType").execute(num_retries=2)
    if root.get("mimeType") != drive.FOLDER_MIME:
        raise ValueError(f"{root_id} is not a folder")
    parent, siblings = None, []
    pids = root.get("parents") or []
    if pids:
        p = svc.files().get(fileId=pids[0], fields="id, name").execute(num_retries=2)
        parent = {"id": p["id"], "name": p["name"]}
        siblings = [f for f in _folders_in(drive, svc, pids[0]) if f["id"] != root_id]
    children = _folders_in(drive, svc, root_id)
    return {"root": {"id": root["id"], "name": root["name"]}, "parent": parent,
            "siblings": siblings, "children": children}


def drive_folder_name(folder_id):
    """(id, name) for a pasted id - validates that it is a folder."""
    drive, svc = _drive_service()
    f = svc.files().get(fileId=folder_id, fields="id, name, mimeType").execute(num_retries=2)
    if f.get("mimeType") != drive.FOLDER_MIME:
        raise ValueError(f"'{f.get('name')}' is a file, not a folder")
    return f["id"], f["name"]


# ── Drive for desktop (local Finder picker) + recent folders ────────────────
def drive_mounts():
    """Local Drive for desktop roots (…/CloudStorage/GoogleDrive-<account>)."""
    try:
        return sorted(p for p in CLOUD_STORAGE.iterdir() if p.name.startswith("GoogleDrive-"))
    except OSError:
        return []


def local_drive_id(path):
    """Drive file id of a folder synced by Drive for desktop, or None (not a
    Drive folder / not synced yet)."""
    r = subprocess.run(["xattr", "-p", DRIVEFS_ID_XATTR, str(path)],
                       capture_output=True, text=True)
    fid = r.stdout.strip()
    return fid if r.returncode == 0 and _DRIVE_ID.fullmatch(fid) else None


def read_recent():
    """[{id, name, path?}] most recent first; [] on any problem."""
    try:
        data = json.loads(RECENT.read_text(encoding="utf-8"))
        return [r for r in data if isinstance(r, dict) and _DRIVE_ID.fullmatch(r.get("id", ""))]
    except Exception:
        return []


def remember_folder(folder_id, name, path=None):
    old = next((r for r in read_recent() if r["id"] == folder_id), {})
    entry = {"id": folder_id, "name": name}
    if path or old.get("path"):
        entry["path"] = str(path or old["path"])
    recent = [entry] + [r for r in read_recent() if r["id"] != folder_id]
    tmp = RECENT.with_name(RECENT.name + ".tmp")
    tmp.write_text(json.dumps(recent[:MAX_RECENT], ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, RECENT)


# ── state ───────────────────────────────────────────────────────────────────
def wrapper_events():
    """[(datetime, message)] for every 'wrapper:' line in launchd.log."""
    if not WRAPPER_LOG.exists():
        return []
    out = []
    for line in WRAPPER_LOG.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _WRAPPER_LINE.match(line)
        if m:
            out.append((dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"), m.group(2)))
    return out


def last_run_event(events):
    """Walk backwards to the most recent run outcome.

    Returns dict(kind, status, run_id, time) where kind is 'finished',
    'aborted' (before the run started), 'interrupted' (started, never
    finished - wrapper killed) or None when the log has no run yet.
    """
    started = None
    for when, msg in reversed(events):
        m = _FINISHED.match(msg)
        if m:
            return {"kind": "finished", "run_id": m.group(1), "status": m.group(2), "time": when}
        for needle, label in _ABORTS.items():
            if needle in msg:
                return {"kind": "aborted", "run_id": None, "status": label, "time": when}
        if msg.startswith(_STARTING):
            started = when
            break
    if started:
        return {"kind": "interrupted", "run_id": None,
                "status": "INTERRUPTED (no RUN SUMMARY)", "time": started}
    return None


def run_log_for(run_id):
    for name in (f"agent_sdk_{run_id}.log", f"agent_{run_id}.log"):
        p = LOGS / name
        if p.exists():
            return p
    return None


def latest_run_log():
    logs = list(LOGS.glob("agent_sdk_*.log")) + list(LOGS.glob("agent_[0-9]*.log"))
    return max(logs, key=lambda p: p.stat().st_mtime) if logs else None


def parse_summary(log_path):
    """RUN SUMMARY block -> [(count_line, [sub_items])]. Count lines use ' : ';
    per-file sub-items start with '   - ' (same split the wrapper uses)."""
    if not log_path:
        return []
    found, rows = False, []
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "RUN SUMMARY" in line:
            found = True
            continue
        if not found:
            continue
        if line.startswith("====="):
            break
        if line.startswith("   - "):
            if rows:
                rows[-1][1].append(line.strip()[2:])
        elif " : " in line:
            rows.append((re.sub(r"\s+", " ", line).strip().replace("—", "-"), []))
    return rows


def wrapper_process():
    """Command line of a live wrapper SHELL process, or None. The pattern is
    anchored on the interpreter so an editor / `less` / `cat` holding
    scripts/run_agent.sh in its argv is not mistaken for a run."""
    r = subprocess.run(["pgrep", "-fl", r"(^|/)(sh|bash|zsh|dash) .*scripts/run_agent\.sh"],
                       capture_output=True, text=True)
    line = r.stdout.strip().splitlines()
    return line[0] if line else None


def wrapper_running():
    # the wrapper creates .run_marker right before it starts the agent and
    # removes it afterwards; a run is "in progress" only when the marker AND
    # the wrapper shell are both present (the no-op / due-check path never
    # creates the marker)
    return MARKER.exists() and wrapper_process() is not None


def _descendants(pid):
    """All descendant pids of pid, depth-first (children before grandchildren)."""
    r = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True)
    out = []
    for c in (int(x) for x in r.stdout.split()):
        out.append(c)
        out.extend(_descendants(c))
    return out


def kill_run(grace=5.0):
    """Kill the agent (and anything it spawned) under the live wrapper, but NOT
    the wrapper shell itself: it sees the KILLED_FLAG dropped here and does its
    normal bookkeeping - logs KILLED, leaves last_run unstamped (the run stays
    due), notifies, removes .run_marker. SIGTERM first, SIGKILL whatever is
    still alive after `grace` seconds. Returns the killed pids."""
    proc = wrapper_process()
    if not proc:
        return []
    wrapper_pid = int(proc.split()[0])
    pids = _descendants(wrapper_pid)
    if pids:
        KILLED_FLAG.touch()

    def signal_all(sig):
        for p in pids:
            try:
                os.kill(p, sig)
            except ProcessLookupError:
                pass

    signal_all(signal.SIGTERM)
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline and set(_descendants(wrapper_pid)) & set(pids):
        time.sleep(0.2)
    signal_all(signal.SIGKILL)
    return pids


def firing_time():
    """(hour, minute) of the daily launchd firing from the agent plist."""
    try:
        cal = plistlib.loads(AGENT_PLIST.read_bytes())["StartCalendarInterval"]
        return int(cal.get("Hour", 0)), int(cal.get("Minute", 0))
    except Exception:
        return 18, 0


def read_stamp():
    try:
        return dt.datetime.fromtimestamp(int(STAMP.read_text().strip()))
    except Exception:
        return None


def next_run(stamp, interval_days, now=None):
    """(next_firing, due_at): the first daily firing at/after the moment the
    wrapper's due-check passes. due_at None = never ran (due immediately)."""
    now = now or dt.datetime.now()
    due_at = stamp + dt.timedelta(days=interval_days) if stamp else None
    start = max(due_at, now) if due_at else now
    h, m = firing_time()
    cand = start.replace(hour=h, minute=m, second=0, microsecond=0)
    if cand < start:
        cand += dt.timedelta(days=1)
    return cand, due_at


def collect_state(drive=None):
    """drive: cached drive_listing() result, {"error": str}, or None (not
    fetched yet) - the GUI fetches it in the background, --print synchronously."""
    cfg_err, cfg = None, {}
    try:
        cfg = read_config()
    except Exception as e:  # keep the app alive on a broken config
        cfg_err = f"{type(e).__name__}: {e}"
    interval = cfg.get("run_interval_days")
    if not isinstance(interval, int) or isinstance(interval, bool) or interval < 1:
        interval = None
    # same degrade rule as the wrapper: anything but a positive int = unlimited
    max_files = cfg.get("max_files_per_run", 0)
    if not isinstance(max_files, int) or isinstance(max_files, bool) or max_files < 0:
        max_files = 0

    events = wrapper_events()
    last = last_run_event(events)
    running = wrapper_running()
    stamp = read_stamp()
    nxt, due_at = next_run(stamp, interval or 7)
    now = dt.datetime.now()

    if running:
        state = "running"
    elif last is None:
        state = "due"
    elif last["kind"] != "finished":
        state = "failed"
    elif last["status"] == "OK":
        state = "due" if (due_at is None or due_at <= now) else "ok"
    elif last["status"] == "DEGRADED":
        state = "degraded"
    else:
        state = "failed"

    summary = parse_summary(run_log_for(last["run_id"])) if last and last["run_id"] else []
    return {
        "state": state, "last": last, "summary": summary, "stamp": stamp,
        "interval": interval, "max_files": max_files, "cfg_err": cfg_err,
        "root_id": cfg.get("root_folder_id"), "drive": drive, "vault": cfg.get("vault_path"),
        "model": cfg.get("model") if isinstance(cfg.get("model"), str) else DEFAULT_MODEL,
        "recent": read_recent(), "local_drive": bool(drive_mounts()),
        "next_run": nxt, "due_at": due_at, "now": now,
        "latest_log": latest_run_log(), "wrapper_log": WRAPPER_LOG,
    }


# ── menu model (plain data, so --print works without a GUI) ─────────────────
def _fmt(t):
    return t.strftime("%a %Y-%m-%d %H:%M") if t else "never"


def menu_spec(s):
    """[(label, children, action[, checked])] - children is a nested list or
    None; action is a string tag handled by the rumps layer; a 4th element
    True marks the item with a checkmark."""
    items = []
    last = s["last"]
    if last is None:
        items.append(("Last run: none yet", None, None))
    else:
        rid = f" (run {last['run_id']})" if last["run_id"] else ""
        head = f"Last run: {_fmt(last['time'])} - {last['status']}{rid}"
        if s["summary"]:
            kids = [(line, [(sub, None, None) for sub in subs] or None, None)
                    for line, subs in s["summary"]]
        elif last["run_id"]:
            kids = [("no RUN SUMMARY in the run log", None, None)]
        else:
            kids = None
        items.append((head, kids, None))

    if s["state"] == "running":
        items.append(("Run in progress...", None, None))
    elif s["due_at"] is None:
        items.append((f"Next run: {_fmt(s['next_run'])} (never ran - due now)", None, None))
    elif s["due_at"] <= s["now"]:
        items.append((f"Next run: {_fmt(s['next_run'])} (due since {_fmt(s['due_at'])})", None, None))
    else:
        items.append((f"Next run: {_fmt(s['next_run'])} (due {_fmt(s['due_at'])})", None, None))
    items.append(None)

    cur = s["interval"]
    choices = sorted(set(INTERVALS) | ({cur} if cur else set()))
    label = f"Run every: {cur} day{'s' if cur != 1 else ''}" if cur else "Run every: ? (config error)"
    kids = [(f"{n} day{'s' if n != 1 else ''}", None, f"interval:{n}", n == cur)
            for n in choices]
    items.append((label, kids, None))

    mf = s["max_files"]
    choices = sorted(set(MAX_FILES_CHOICES) | {mf})
    label = f"Max files per run: {mf if mf else 'unlimited'}"
    kids = [(f"{n} file{'s' if n != 1 else ''}" if n else "Unlimited", None, f"maxfiles:{n}", n == mf)
            for n in choices]
    items.append((label, kids, None))

    cur = s["model"]
    choices = list(MODELS) + ([] if any(m == cur for m, _ in MODELS) else [(cur, cur)])
    kids = [(f"{lbl}  ({m})" if lbl != m else m, None, f"model:{m}", m == cur) for m, lbl in choices]
    kids += [None, ("Other model id…", None, "model_enter")]
    items.append((f"Model: {model_label(cur)}", kids, None))

    items.append(_drive_item(s))
    if s["cfg_err"]:
        items.append((f"config.json: {s['cfg_err'][:80]}", None, None))
    # always clickable - a false "running" reading must never lock the user
    # out; the click path re-checks and explains instead
    items.append(("Run now" if s["state"] != "running" else "Run now (a run is in progress)",
                  None, "run_now"))
    if s["state"] == "running":
        items.append(("Kill run", None, "kill_run"))
    items.append(None)
    items.append(("Open vault", None, "open_vault"))
    items.append(("Open latest log", None, "open_log"))
    items.append(("Open wrapper log", None, "open_wrapper_log"))
    items.append(None)
    items.append(("Quit", None, "quit"))
    return items


def _drive_item(s):
    """'Drive folder: <name>' with a chooser submenu: Finder picker, recent
    folders, parent, sibling folders, subfolders (each selectable),
    paste-an-id, open in browser, refresh."""
    d, rid = s["drive"], s["root_id"]
    short = f"{rid[:6]}…" if rid else "?"
    top = []
    if s.get("local_drive"):
        top.append(("Choose folder in Finder…", None, "root_pick"))
    recent = [r for r in s.get("recent", []) if r["id"] != rid]
    if recent:
        top.append(("Recent folders:", None, None))
        top += [(f"    {r['name']}", None, f"root:{r['id']}") for r in recent]
    if top:
        top.append(None)
    if d and "root" in d and d["root"]["id"] == rid:
        label = f"Drive folder: {d['root']['name']}"
        kids = top + [(f"{d['root']['name']}  ({short})", None, None, True), None]
        if d["parent"]:
            kids.append((f"⬆ Up to: {d['parent']['name']}", None, f"root:{d['parent']['id']}"))
        if d["siblings"]:
            kids.append(("Other folders here:", None, None))
            kids += [(f"    {f['name']}", None, f"root:{f['id']}") for f in d["siblings"][:40]]
        if d["children"]:
            kids.append(("Subfolders:", None, None))
            kids += [(f"    {f['name']}", None, f"root:{f['id']}") for f in d["children"][:40]]
        kids.append(None)
    elif d and "error" in d:
        label = f"Drive folder: {short} (Drive unavailable)"
        kids = top + [(f"Drive error: {d['error'][:90]}", None, None), None]
    else:
        label = f"Drive folder: {short} (loading…)" if rid else "Drive folder: not set"
        kids = top
    kids += [("Enter folder URL or id…", None, "root_enter"),
             ("Open in Google Drive", None, "open_drive"),
             ("Refresh folder list", None, "root_refresh")]
    return (label, kids, None)


def render_text(s, items=None, depth=0):
    items = menu_spec(s) if items is None else items
    out = []
    if depth == 0:
        icon = "icon " + ICON.name if ICON.exists() else "no icon"
        out.append(f"title: {status_title(s['state']) or '(icon only)'}   (state={s['state']}, {icon})")
    for it in items:
        if it is None:
            out.append("  " * depth + "---")
            continue
        label, kids = it[0], it[1]
        checked = len(it) > 3 and it[3]
        out.append("  " * depth + ("✓ " if checked else "") + label)
        if kids:
            out.extend(render_text(s, kids, depth + 1))
    return out


# ── rumps layer ─────────────────────────────────────────────────────────────
def _log(msg):
    """Audit line in logs/menubar.err (launchd's StandardErrorPath)."""
    print(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} menubar: {msg}", file=sys.stderr, flush=True)


def front_alert(title, message, buttons):
    """Modal alert that actually shows up: a menu-bar-only (background) app is
    never the active app, so rumps.alert's NSAlert opens BEHIND other windows.
    Activate first and float the window. Returns the index of the pressed
    button (buttons[0] is the default / Return key)."""
    from AppKit import NSAlert, NSApplication, NSFloatingWindowLevel
    NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
    alert = NSAlert.alloc().init()
    alert.setMessageText_(title)
    alert.setInformativeText_(message)
    for b in buttons:
        alert.addButtonWithTitle_(b)
    alert.window().setLevel_(NSFloatingWindowLevel)
    return alert.runModal() - 1000   # NSAlertFirstButtonReturn == 1000


def front_prompt(title, message, default=""):
    """Front-most text prompt (NSAlert with a text-field accessory). Returns the
    entered text, or None on Cancel."""
    from AppKit import NSAlert, NSApplication, NSFloatingWindowLevel, NSTextField
    from Foundation import NSMakeRect
    NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
    alert = NSAlert.alloc().init()
    alert.setMessageText_(title)
    alert.setInformativeText_(message)
    alert.addButtonWithTitle_("OK")
    alert.addButtonWithTitle_("Cancel")
    field = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 420, 24))
    field.setStringValue_(default)
    alert.setAccessoryView_(field)
    alert.window().setLevel_(NSFloatingWindowLevel)
    alert.window().setInitialFirstResponder_(field)
    if alert.runModal() != 1000:
        return None
    return str(field.stringValue())


def pick_local_folder(start=None):
    """Native folder picker (NSOpenPanel, directories only) opened in the Drive
    for desktop tree. Returns the chosen path, or None on Cancel."""
    from AppKit import NSApplication, NSFloatingWindowLevel, NSOpenPanel
    from Foundation import NSURL
    NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
    panel = NSOpenPanel.openPanel()
    panel.setCanChooseDirectories_(True)
    panel.setCanChooseFiles_(False)
    panel.setAllowsMultipleSelection_(False)
    panel.setCanCreateDirectories_(False)
    panel.setTitle_("Drive folder to scan")
    panel.setMessage_("Pick the Google Drive folder the next Targum run should scan.")
    panel.setPrompt_("Use this folder")
    if start:
        panel.setDirectoryURL_(NSURL.fileURLWithPath_(str(start)))
    panel.setLevel_(NSFloatingWindowLevel)
    if panel.runModal() != 1:   # NSModalResponseOK
        return None
    return pathlib.Path(str(panel.URLs()[0].path()))


def _picker_start():
    """Where the Finder picker opens: next to the last folder picked through
    it, else the first account's My Drive."""
    for r in read_recent():
        p = pathlib.Path(r.get("path", ""))
        if r.get("path") and p.exists():
            return p.parent
    mounts = drive_mounts()
    if not mounts:
        return None
    my = mounts[0] / "My Drive"
    return my if my.exists() else mounts[0]


def _signature():
    def mt(p):
        try:
            return p.stat().st_mtime
        except OSError:
            return None
    return (mt(WRAPPER_LOG), mt(CONFIG), mt(STAMP), mt(RECENT), MARKER.exists())


def main_app():
    import rumps

    class TargumApp(rumps.App):
        def __init__(self):
            icon = str(ICON) if ICON.exists() else None
            super().__init__("Targum", title=None if icon else BASE_TITLE,
                             icon=icon, template=True, quit_button=None)
            self._sig = None
            self._state = None
            # Drive listing cache: fetched off the main thread (network), the
            # result parked in _pending and applied by the 1s timer (UI work
            # must stay on the main thread)
            self._drive = None          # listing / {"error"} for _drive_for
            self._drive_for = None      # root id the cache belongs to
            self._fetching = None       # root id currently being fetched
            self._pending = None        # (root_id, result) from the thread
            self.rebuild()
            rumps.Timer(self.tick, 60).start()
            rumps.Timer(self.apply_pending, 1).start()

        def tick(self, _timer):
            sig = _signature()
            # while a run is in progress the process check is the only signal
            if sig != self._sig or (self._state and self._state["state"] == "running"):
                self.rebuild()

        # ── Drive listing (background) ───────────────────────────────────
        def fetch_drive(self, root_id):
            if not root_id or self._fetching == root_id:
                return
            self._fetching = root_id

            def work():
                try:
                    res = drive_listing(root_id)
                except Exception as e:
                    _log(f"drive listing failed: {type(e).__name__}: {e}")
                    res = {"error": f"{type(e).__name__}: {e}"}
                self._pending = (root_id, res)
            threading.Thread(target=work, daemon=True).start()

        def apply_pending(self, _timer):
            if self._pending is None:
                return
            root_id, res = self._pending
            self._pending = None
            self._fetching = None
            self._drive, self._drive_for = res, root_id
            self.rebuild()

        def invalidate_drive(self):
            self._drive = self._drive_for = None

        def rebuild(self):
            self._sig = _signature()
            try:
                root_id = read_config().get("root_folder_id")
            except Exception:
                root_id = None
            drive = self._drive if (root_id and self._drive_for == root_id) else None
            if root_id and drive is None:
                self.fetch_drive(root_id)
            try:
                s = collect_state(drive=drive)
                spec = menu_spec(s)
                title = status_title(s["state"])
            except Exception:
                traceback.print_exc()
                s = None
                spec = [(f"menubar error - see logs/menubar.err", None, None), None,
                        ("Open wrapper log", None, "open_wrapper_log"), ("Quit", None, "quit")]
                title = _base_title() + "❓"   # error state is always shown
            self._state = s
            self.title = title
            self.menu.clear()
            self.menu = self._build(spec)

        def _build(self, spec):
            out = []
            for it in spec:
                if it is None:
                    out.append(None)
                    continue
                label, kids, action = it[0], it[1], it[2]
                mi = rumps.MenuItem(label, callback=self._cb(action) if action else None)
                mi.state = 1 if (len(it) > 3 and it[3]) else 0
                for k in (kids or []):
                    mi.add(k if k is None else self._build([k])[0])
                out.append(mi)
            return out

        def _cb(self, action):
            def cb(_item):
                try:
                    self.act(action)
                except Exception as e:
                    traceback.print_exc()
                    front_alert("Targum menubar", f"{type(e).__name__}: {e}", ["OK"])
            return cb

        def _folder_name(self, folder_id):
            d = (self._state or {}).get("drive") or {}
            for f in ([d.get("root"), d.get("parent")] + d.get("siblings", []) + d.get("children", [])
                      + read_recent()):
                if f and f["id"] == folder_id:
                    return f["name"]
            return folder_id

        def change_root(self, folder_id, name, path=None, confirm=True):
            cur = (self._state or {}).get("root_id")
            if folder_id == cur:
                return
            choice = 1 if not confirm else front_alert(f"Scan Drive folder '{name}'?",
                                 "The next run scans this folder (and its subfolders) "
                                 f"instead of '{self._folder_name(cur)}'.\n\nFiles already "
                                 "translated stay recorded; nothing is deleted.",
                                 ["Cancel", "Use this folder"])
            if choice != 1:
                _log(f"root change to {folder_id} cancelled")
                return
            if cur:   # keep the folder we leave one click away
                remember_folder(cur, self._folder_name(cur))
            write_root_folder(folder_id)
            remember_folder(folder_id, name, path)
            _log(f"root_folder_id -> {folder_id} ({name})")
            self.invalidate_drive()
            self.rebuild()

        def act(self, action):
            if action.startswith("interval:"):
                write_interval(action.split(":")[1])
                self.rebuild()
            elif action.startswith("maxfiles:"):
                write_max_files(action.split(":")[1])
                self.rebuild()
            elif action.startswith("model:"):
                write_model(action[len("model:"):])
                _log(f"model -> {action[len('model:'):]}")
                self.rebuild()
            elif action == "model_enter":
                text = front_prompt("Claude model",
                                    "Model id for the next run (e.g. claude-opus-5-5):",
                                    (self._state or {}).get("model") or "")
                if text:
                    write_model(text)
                    _log(f"model -> {text.strip()}")
                    self.rebuild()
            elif action == "root_pick":
                path = pick_local_folder(_picker_start())
                if path is None:
                    return
                fid = local_drive_id(path)
                if not fid:
                    front_alert("Targum", f"Not a Google Drive folder (or not synced yet):\n{path}\n\n"
                                "Pick a folder inside the Google Drive location in Finder's sidebar.",
                                ["OK"])
                    return
                # choosing in the picker is the confirmation - no second dialog
                self.change_root(fid, path.name, path=path, confirm=False)
            elif action.startswith("root:"):
                fid = action[len("root:"):]
                self.change_root(fid, self._folder_name(fid))
            elif action == "root_enter":
                cur = (self._state or {}).get("root_id") or ""
                text = front_prompt("Drive folder to scan",
                                    "Paste a Google Drive folder URL or folder id:", cur)
                if text is None:
                    return
                fid = parse_drive_folder(text)
                if not fid:
                    front_alert("Targum", f"Not a Drive folder URL or id:\n{text}", ["OK"])
                    return
                fid, name = drive_folder_name(fid)   # validates it is a folder
                self.change_root(fid, name)
            elif action == "root_refresh":
                self.invalidate_drive()
                self.rebuild()
            elif action == "open_drive":
                rid = (self._state or {}).get("root_id")
                if rid:
                    subprocess.run(["open", f"https://drive.google.com/drive/folders/{rid}"])
            elif action == "run_now":
                _log("run_now clicked")
                if wrapper_running():
                    proc = wrapper_process()
                    _log(f"run_now refused - run in progress: {proc}")
                    front_alert("Targum: a run is already in progress",
                                f"Wrapper process: {proc}\n\nWait for it to finish "
                                "(\"Run now\" is refused while it runs) before starting another.", ["OK"])
                    return
                # Cancel is the first (default, Return-key) button: a stray
                # keypress must never start a billable run.
                choice = front_alert("Start a Targum run now?",
                                     "This starts a REAL translation run (bypasses the due-check; "
                                     "it costs money/quota). Continue?",
                                     ["Cancel", "Run now"])
                if choice != 1:
                    _log("run_now cancelled")
                    return
                with open(WRAPPER_LOG, "a") as log:
                    p = subprocess.Popen(["/bin/sh", str(WRAPPER), "--force"], cwd=ROOT,
                                         stdout=log, stderr=log, start_new_session=True)
                _log(f"run_now confirmed - launched run_agent.sh --force (pid {p.pid})")
                rumps.notification("Targum", "", "Run started - the wrapper notifies when done.")
                self.title = status_title("running")
            elif action == "kill_run":
                _log("kill_run clicked")
                proc = wrapper_process()
                if not proc:
                    front_alert("Targum", "No run is in progress.", ["OK"])
                    self.rebuild()
                    return
                # Cancel first (default, Return-key) - same rule as Run now
                choice = front_alert("Kill the running Targum run?",
                                     f"Wrapper process: {proc}\n\nThe agent is stopped; files "
                                     "already translated stay translated, the rest are re-offered "
                                     "next run (the run stays due).",
                                     ["Cancel", "Kill run"])
                if choice != 1:
                    _log("kill_run cancelled")
                    return
                pids = kill_run()
                _log(f"kill_run confirmed - killed {pids or 'nothing'} under {proc}")
                self.rebuild()
                vault = (self._state or {}).get("vault")
                if vault:
                    subprocess.run(["open", vault])
                else:
                    front_alert("Targum", "vault_path not set in config.json", ["OK"])
            elif action == "open_log":
                p = (self._state or {}).get("latest_log") or WRAPPER_LOG
                subprocess.run(["open", "-t", str(p)])
            elif action == "open_wrapper_log":
                subprocess.run(["open", "-t", str(WRAPPER_LOG)])
            elif action == "quit":
                rumps.quit_application()

    TargumApp().run()


if __name__ == "__main__":
    if "--print" in sys.argv[1:]:
        # synchronous Drive lookup here (the app does it in the background)
        try:
            drive = drive_listing(read_config().get("root_folder_id"))
        except Exception as e:
            drive = {"error": f"{type(e).__name__}: {e}"}
        print("\n".join(render_text(collect_state(drive=drive))))
    else:
        main_app()
