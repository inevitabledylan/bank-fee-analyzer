"""Central settings for the Bank Fee Analyzer.

Everything adjustable lives here so the rest of the code never hardcodes
a file path, rate, or threshold. Values can be overridden in the .env file.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Folders
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
REFERENCE_DIR = PROJECT_ROOT / "reference"
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
CACHE_DIR = DATA_DIR / "cache"
MEMO_CACHE_DIR = CACHE_DIR / "memos"

# Load .env from the project root (does nothing if the file is missing).
load_dotenv(PROJECT_ROOT / ".env")


def _env_float(name: str, default: float) -> float:
    """Read a number from the environment, falling back to a default."""
    value = os.getenv(name, "").strip()
    return float(value) if value else default


# ---------------------------------------------------------------------------
# AI settings
# ---------------------------------------------------------------------------
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "").strip() or "claude-haiku-5-5"
LLM_TEMPERATURE = 0.0  # low temperature = consistent, repeatable answers


def has_api_key() -> bool:
    """True if an Anthropic API key is configured. Never returns the key itself."""
    return bool(os.getenv("ANTHROPIC_API_KEY", "").strip())


# ---------------------------------------------------------------------------
# Synthetic data
# ---------------------------------------------------------------------------
RANDOM_SEED = 42  # fixed so the generated data is identical on every run
PERIOD_START = "2025-10-01"
PERIOD_END = "2026-09-01"  # first day of the last statement month

# ---------------------------------------------------------------------------
# Treasury assumptions
# ---------------------------------------------------------------------------
# Yield the company could earn on idle cash instead (money market fund / sweep).
MONEY_MARKET_YIELD = _env_float("MONEY_MARKET_YIELD", 0.04)

# ---------------------------------------------------------------------------
# AFP mapping thresholds (SPEC.md §3)
# ---------------------------------------------------------------------------
FUZZY_ACCEPT_SCORE = 90        # fuzzy score at or above this is accepted without AI
REVIEW_CONFIDENCE = 0.70       # AI confidence below this is flagged for human review

# ---------------------------------------------------------------------------
# Anomaly detection thresholds (SPEC.md §4, DECISIONS.md)
# ---------------------------------------------------------------------------
PRICE_INCREASE_THRESHOLD = 0.05   # month-over-month unit price increase > 5%
UNUSED_SERVICE_MIN_MONTHS = 3     # zero related activity for 3+ consecutive months
MATH_ERROR_TOLERANCE = 0.01       # |volume x price - total| above 1 cent
EXCESS_BALANCE_THRESHOLD = 0.20   # excess balance > 20% of balance required...
EXCESS_BALANCE_MIN_MONTHS = 3     # ...for 3+ months
MONTHS_PER_YEAR = 12              # used to annualize run-rate findings
