"""All settings in one place: file paths and model settings."""
from __future__ import annotations

import os
from pathlib import Path

# The project folder (the folder that contains qra/).
ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """Read KEY=VALUE lines from the local .env file (git-ignored). Real environment variables win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("export "):  # also accept shell-style "export KEY=value" lines
            line = line[len("export "):]
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


# Load .env before reading any setting below, so values in .env are always used.
load_dotenv()


def _project_path(value: str) -> Path:
    """Relative paths are taken from the project folder, not from wherever the command is run."""
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


# --- Data files ---
STARTER_SEED = ROOT / "data" / "starter" / "seed.json"
ADDITION_DOCUMENTS = ROOT / "data" / "additions" / "documents.json"
QUESTIONNAIRE_B = ROOT / "data" / "additions" / "questionnaire-b.json"
SOURCE_CHANGE_SCENARIO = ROOT / "data" / "additions" / "source-change-support-v1-version-2.json"
REFERENCE_CASES = ROOT / "reference" / "reference-cases.json"
REPLAY_DIR = ROOT / "replays"
PROMPT_DIR = ROOT / "qra" / "prompts"
DEFAULT_DB = _project_path(os.environ.get("QRA_DB", "var/qra.sqlite"))

# --- Model settings (recorded in ai-workflow/manifest.json) ---
MODEL = os.environ.get("QRA_MODEL", "claude-opus-5-5")
DRAFT_EFFORT = os.environ.get("QRA_DRAFT_EFFORT", "medium")
VERIFY_EFFORT = os.environ.get("QRA_VERIFY_EFFORT", "medium")
MAX_TOKENS = 16000
REQUEST_TIMEOUT_SECONDS = 120.0
MAX_RETRIES = 2
# If a safety filter declines a request, the API retries it on a fallback model and says so.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# --- Model modes ---
#   replay: only use saved responses from replays/ (no API key needed) - the default
#   live:   always call the API and save the response
#   auto:   use a saved response if there is one, otherwise call the API
MODES = ("replay", "live", "auto")
DEFAULT_MODE = os.environ.get("QRA_MODEL_MODE", "replay")
