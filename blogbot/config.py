"""config.yaml 로딩과 프로젝트 경로.

프로젝트 루트는 config.yaml 이 있는 폴더다. 어디서 실행하든 data/state/output 이
설정 파일 옆에서 찾아진다.
"""

from __future__ import annotations

import os
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
    def style_stats(self) -> Path:
        return self.state / "style_stats.json"

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
    def browser_profile(self) -> Path:
        return self.root / ".auth" / "browser-profile"


def _load_dotenv(path: Path) -> None:
    """.env 의 KEY=VALUE 를 환경변수로 (이미 설정된 값은 덮어쓰지 않음)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_config(path: str | Path = "config.yaml") -> dict[str, Any]:
    global ROOT
    path = Path(path).resolve()
    if not path.exists():
        raise SystemExit(
            f"{path} 가 없습니다. config.example.yaml 을 복사해 config.yaml 을 만들어 주세요."
        )
    ROOT = path.parent
    _load_dotenv(ROOT / ".env")
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def paths() -> Paths:
    return Paths(ROOT)
