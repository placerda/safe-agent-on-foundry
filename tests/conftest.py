from __future__ import annotations

import sys
from pathlib import Path


AGENT_ROOT = Path(__file__).resolve().parents[1] / "src" / "helpdeskbot"
sys.path.insert(0, str(AGENT_ROOT))

