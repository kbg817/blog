"""config.yaml 로딩과 프로젝트 경로."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

ROOT = Path.cwd()


@dataclass
class Paths:
    root: Path

    @property
    def manuscripts(self) -> Path:
        return self.root / "data" / "manuscripts"

    @property
    def photos(self) -> Path:
        return self.root / "data" / "photos"

    @property
    def state(self) -> Path:
        return self.root / "state"

    @property
    def style_profile(self) -> Path:
        return self.state / "style_profile.json"

    @property
    def photo_index(self) -> Path:
        return self.state / "photo_index.json"

    @property
    def ledger(self) -> Path:
        return self.state / "ledger.json"

    @property
    def drafts(self) -> Path:
        return self.root / "output" / "drafts"

    @property
    def auth(self) -> Path:
        return self.root / ".auth" / "naver_state.json"


def load_config(path: str | Path = "config.yaml") -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        raise SystemExit(
            f"{path} 가 없습니다. config.example.yaml 을 복사해 {path} 를 만들어 주세요."
        )
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def paths() -> Paths:
    return Paths(ROOT)
