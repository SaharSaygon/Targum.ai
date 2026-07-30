# Plan: subscription billing + cron automation

*Drafted 2026-07-27. Revises the original Phase 3 design (GitHub Actions cron) in
`hebrew_translator_project_plan.md` in light of two new goals:*
1. *Run the agent against the owner's Claude Pro/Max subscription usage instead of API pay-per-token.*
2. *Automate runs on a schedule.*

---

## Task 1 — Bill runs to the Claude subscription

### The mechanism (verified 2026-07-27)

- The **Claude Agent SDK** (Python `claude-agent-sdk`) runs the Claude Code harness as a
  library and supports subscription auth: run `claude setup-token` once (browser OAuth,
  1-year token) and export `CLAUDE_CODE_OAUTH_TOKEN`. Usage then draws from the Pro/Max
  subscription's 5-hour rolling window + weekly cap — **no API charges**.
  Docs: https://code.claude.com/docs/en/agent-sdk/python.md
- The May 2026 plan to move Agent SDK usage to separate paid credits was **cancelled**
  before its June 15 effective date; Agent SDK usage still draws from the plan.
  https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan
- Custom tools are supported in-process: `@tool` decorator + `create_sdk_mcp_server`,
  wired via `ClaudeAgentOptions(mcp_servers=..., allowed_tools=[...])`.
- ToS caveat (Feb 2026 Commercial Terms update): the OAuth token is for **Claude model
  access only**. Our Google credentials stay entirely separate (token.json as today), so
  the intended use is the documented one — but re-check the terms before building.

### The crux: translation must move into the session

Today the cost has two layers: routing (the agent loop, cheap since the pre-pass) and
**translation (dominant — separate `anthropic.messages.create` calls inside
`translation_engine.py`)**. Porting only the loop to the Agent SDK would leave the
expensive half on API billing. To capture the win, translation itself must be performed
by the subscription-billed session.

**Chosen shape — subagent-per-file (option C):**
- Main session = the routing loop: receives the pre-pass worklist, applies the routing
  rules (`agent_routing_prompt.md` becomes the system prompt), calls the same manifest /
  drive / dedup tools.
- For each file to translate, the main agent dispatches a **subagent** whose prompt is
  the existing skill (`skills/translate-*.md`) + the file content (extracted text, or
  page images for image mode). The subagent writes the translated markdown **directly via
  the Write tool** to the vault path — this sidesteps single-response output caps (the
  64K-streaming truncation class of bugs disappears; long lectures become multiple Write
  calls). Main agent then records the manifest entry.
- Rejected alternatives: (A) hybrid billing — keeps ~90% of cost on the API;
  (B) translate in the main context — blows up the routing context and re-couples the
  two concerns.

### Port plan (deterministic modules unchanged)

Keep as-is: `prepass.py`, `dedup.py`, `manifest.py`, `drive.py`, `config.py`,
`pdf_mode_detector.py`, `skills/`, `translated_log.json` schema.

New/changed:
1. `agent_sdk.py` (new entry point; `agent.py` stays as the API-key path) — builds the
   worklist via the pre-pass exactly as `agent.py:581` does, then drives
   `claude_agent_sdk.query()` with:
   - `system_prompt` = kickoff + routing rules (courses.json injected as today),
   - in-process MCP server exposing the non-translate tools (`list_folder`, `read_file`,
     `save_to_vault`, `update_mapping`, `fetch_signal_detail`, `skip_file`) — thin wrappers
     around the existing handler functions,
   - built-in tools restricted to what's needed (`Task` for subagents, `Write` scoped to
     the vault, no Bash),
   - translate-subagent definition per the shape above.
2. `config.json`: `auth_mode: "subscription" | "api"`, **default `"subscription"`**
   (Agent SDK on the Claude plan); `"api"` keeps the existing `agent.py` path as the
   tested fallback. Also overridable per-run via CLI flag.
3. Cost accounting: `costs.py` ledger becomes advisory under subscription (report token
   usage from SDK messages; dollars no longer apply). `spend_cap_usd` applies only to
   `auth_mode: "api"`.
4. Manifest `model` field: new value (e.g. `"subscription-opus"` + actual model id) so
   history stays interpretable.

### Fit check against plan limits

Routine incremental runs (2–9 files) fit comfortably in one 5-hour window on Pro.
Catch-up/influx runs (26–50 files, like July 16/20) may exhaust a Pro window mid-run —
mitigation: the manifest checkpoint means a rate-limited run just resumes next window
(pre-pass drops everything already saved). On Max this is a non-issue. Verify with the
first live run before scheduling.

---

## Task 2 — Automate on a schedule

### Options considered

| Option | Verdict | Why |
|---|---|---|
| **launchd (local macOS)** | **Chosen** | Everything the agent needs is local: `token.json` (whose `invalid_grant` fallback is *interactive* OAuth — impossible headless in CI), the Obsidian vault on disk, the git repo. launchd (unlike cron) runs a missed `StartCalendarInterval` job once on wake, which fits a laptop. |
| GitHub Actions cron (original Phase 3) | Deferred | Requires vault-as-repo + Google secrets in CI, and the interactive-OAuth refresh fallback breaks headless. Revisit only if laptop-independence becomes a real need. |
| Claude Code routines / Managed Agents (cloud) | Rejected for now | Sandbox has no access to the local vault; would force vault-as-repo restructure + credential vault for Google. More moving parts than the problem warrants. |

### Build plan

1. **Prerequisites for unattended runs** (do these first):
   - `spend_cap_usd` enforcement in the loop (guards the API fallback path).
   - `skipped_transient` + `error_message` manifest states (plan item 2.6 second half) so
     a transient failure is retried next run instead of lost.
   - **Run-summary notification** (plan items 2.8/2.9): end-of-run summary (translated /
     skipped / errors / cost-or-tokens) delivered via email (Resend) or macOS
     notification. Unattended runs must be visible. The `costs.py` roll-up already
     produces the content.
2. **Choosable cadence** — decided 2026-07-27: cadence lives in `config.json` as
   `run_interval_days: <int>` (e.g. 7 over the summer, 2 during the semester — any
   positive integer). launchd fires **daily**; the wrapper script reads
   `run_interval_days` + the last-run timestamp (from a `logs/last_run` stamp file) and
   exits immediately if fewer than that many days have passed. This keeps the plist
   static — changing cadence is a one-number config edit, no `launchctl` reload.
   (launchd can't natively express "every N days" anyway, so due-checking in the wrapper
   is also the simpler implementation.)
3. **Wrapper script** `scripts/run_agent.sh`: cadence due-check (above) → cd to repo →
   source env (`CLAUDE_CODE_OAUTH_TOKEN` from Keychain via
   `security find-generic-password`; `.env` for API mode) → run the agent (auth_mode from
   config, default subscription) → append exit status to the log → fire the summary notification →
   commit `translated_log.json` (Phase 3.6's intent, done locally).
4. **launchd plist** `~/Library/LaunchAgents/ai.targum.agent.plist`:
   `StartCalendarInterval` daily at a fixed hour, stdout/err to `logs/launchd.log`.
   Missed-while-asleep firings run once on wake.
5. **First scheduled runs supervised**: watch 2–3 firings before trusting it.

### Why not a Claude schedule (cloud routines)? — asked & answered 2026-07-27

Not a scheduler problem — a *locality* problem. A scheduled Claude cloud agent runs in a
sandbox that can mount the git repo but not: (1) the local Obsidian vault (`vault_path`
is a folder on the Mac — cloud writes would require vault-as-repo + Obsidian Git
auto-pull, i.e. the original Phase 3 machinery); (2) the interactive Google OAuth
fallback (`invalid_grant` → browser flow, added `fbf419c` — impossible headless, so a
revoked refresh token would fail every run until manual re-auth + secret re-upload);
(3) the gitignored `credentials.json`/`token.json`, which would have to be provisioned
into the cloud environment. launchd runs where all three already live. A cloud schedule
remains the right *upgrade path* if laptop-independence ever matters — prerequisite:
vault-as-repo migration + cloud secret provisioning.

### Sequencing

1. Commit the pending state (STATUS "Next step" — still outstanding, now including the
   July 24 run).
2. Task 1 port (subscription) — changes the runtime, so do it before automating.
3. Task 2 prerequisites (cap, transient states, notification).
4. launchd rollout.

### Decisions made (2026-07-27)

- **Cadence is choosable as a number of days**: `run_interval_days: <int>` in
  `config.json` (7 for summer, 2 for semester were just examples); daily launchd trigger
  + wrapper due-check implements it.
- **Auth is choosable, Subscription default**: `auth_mode: "subscription" | "api"`,
  default `"subscription"`; `"api"` remains the fallback path.
- **Scheduler: launchd confirmed** (over cron / GitHub Actions / Claude cloud schedules —
  rationale below).

### Still open for the owner

- **Pro vs Max**: if on Pro, accept that influx runs may span two 5-hour windows
  (auto-resume makes this safe, just slower). Max removes the concern.
- **ToS comfort**: the Feb 2026 terms restrict OAuth-token use with third-party tools;
  our use (token for Claude access only, Google creds separate) matches the documented
  CI/headless use case, but the owner should skim the current terms once before relying
  on it.
