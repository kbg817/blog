"""사진 인덱스(무슨 사진인지 설명)와 사용 기록 관리."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, Field

from blogbot import llm


class PhotoDescription(BaseModel):
    description: str = Field(description="사진에 무엇이 보이는지 1~2문장")
    keywords: list[str] = Field(description="이 사진이 어울리는 주제 키워드 3~6개")


def file_hash(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()[:16]


def load_json(path: Path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def index_photos(photos: list[Path], root: Path, index_path: Path, model: str) -> dict:
    """새로 추가된 사진만 Claude 비전으로 설명을 붙여 인덱스에 저장한다."""
    index: dict = load_json(index_path, {})
    for p in photos:
        key = str(p.relative_to(root))
        h = file_hash(p)
        if key in index and index[key].get("hash") == h:
            continue
        print(f"  사진 분석: {key}")
        desc = llm.parse(
            model=model,
            system="블로그에 쓸 사진을 분류하는 도우미입니다. 한국어로 간결하게 답하세요.",
            content=[llm.image_block(p), {"type": "text", "text": "이 사진을 설명하세요."}],
            output=PhotoDescription,
            effort="low",
            max_tokens=2000,
        )
        index[key] = {"hash": h, **desc.model_dump()}
        save_json(index_path, index)
    # 삭제된 사진 정리
    existing = {str(p.relative_to(root)) for p in photos}
    for key in list(index):
        if key not in existing:
            del index[key]
    save_json(index_path, index)
    return index


class Ledger:
    """사진 사용 횟수와 발행 기록. 같은 사진의 과도한 재사용과 과도한 발행을 막는다."""

    def __init__(self, path: Path):
        self.path = path
        self.data = load_json(path, {"photo_uses": {}, "posts": []})

    def save(self) -> None:
        save_json(self.path, self.data)

    def photo_available(self, key: str, cooldown_days: int, max_uses: int) -> bool:
        uses = self.data["photo_uses"].get(key, [])
        if len(uses) >= max_uses:
            return False
        if uses:
            last = datetime.fromisoformat(uses[-1])
            if datetime.now() - last < timedelta(days=cooldown_days):
                return False
        return True

    def record_photos(self, keys: list[str]) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        for k in keys:
            self.data["photo_uses"].setdefault(k, []).append(now)

    def posts_today(self) -> int:
        today = datetime.now().date().isoformat()
        return sum(1 for p in self.data["posts"] if p["at"].startswith(today))

    def record_post(self, draft_dir: str, title: str, mode: str) -> None:
        self.data["posts"].append(
            {
                "at": datetime.now().isoformat(timespec="seconds"),
                "draft": draft_dir,
                "title": title,
                "mode": mode,
            }
        )
