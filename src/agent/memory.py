"""Run-scoped durable context shared between pipeline stages."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List


class RunMemory:
    def __init__(self, run_directory: Path):
        self.path = run_directory / "memory.json"
        if self.path.exists():
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            self.entries = loaded if isinstance(loaded, list) else []
        else:
            self.entries: List[Dict[str, Any]] = []

    def record(self, stage: str, payload: Dict[str, Any]) -> None:
        self.entries.append(
            {
                "stage": stage,
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                "payload": payload,
            }
        )
        self.path.write_text(
            json.dumps(self.entries, indent=2) + "\n", encoding="utf-8"
        )

    def context(self) -> str:
        return json.dumps(self.entries, indent=2)
