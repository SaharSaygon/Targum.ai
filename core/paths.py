"""Single source of truth for repo-root-anchored paths.

Every module that touches a file at the repo root resolves it through these
constants. Nothing here may depend on the current working directory — the
legacy agent, the SDK agent, tests, and (later) launchd all start from
different CWDs.
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

CONFIG_PATH = PROJECT_ROOT / "config.json"
COURSES_PATH = PROJECT_ROOT / "courses.json"
LOG_PATH = PROJECT_ROOT / "translated_log.json"          # the manifest
TOKEN_PATH = PROJECT_ROOT / "token.json"                 # Google OAuth token
CREDENTIALS_PATH = PROJECT_ROOT / "credentials.json"     # Google OAuth client
ENV_PATH = PROJECT_ROOT / ".env"
LOGS_DIR = PROJECT_ROOT / "logs"
SKILLS_DIR = PROJECT_ROOT / "skills"
ROUTING_PROMPT_PATH = PROJECT_ROOT / "agent_routing_prompt.md"
