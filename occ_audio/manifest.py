"""Run folders and the run manifest — mirrors occ's ``manifest.py``."""
from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path


def new_run_dir(output_dir: Path, run_name: str | None) -> Path:
    runs = Path(output_dir) / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    name = run_name or time.strftime("run_%Y%m%d_%H%M%S")
    run_dir = runs / name
    run_dir.mkdir(parents=True, exist_ok=True)
    for sub in ("segments", "prompts", "results"):
        (run_dir / sub).mkdir(exist_ok=True)
    return run_dir


def latest_run_dir(output_dir: Path) -> Path | None:
    runs = Path(output_dir) / "runs"
    if not runs.is_dir():
        return None
    candidates = [d for d in runs.iterdir() if d.is_dir()]
    return max(candidates, key=lambda d: d.stat().st_mtime) if candidates else None


def write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@dataclass
class RunManifest:
    run_dir: Path
    meta: dict = field(default_factory=dict)
    segments: list[dict] = field(default_factory=list)

    def add_segment(self, record: dict) -> None:
        self.segments.append(record)
        self.save()

    def save(self) -> None:
        write_json(self.run_dir / "manifest.json",
                   {"run": self.meta, "segments": self.segments})


def log(message: str) -> None:
    print(f"[occ-audio] {message}", file=sys.stderr, flush=True)
