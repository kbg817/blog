"""사진 인덱스(무슨 사진인지 설명)와 사용 기록(ledger) 관리.

사진은 파일 내용 해시로 식별한다. 파일 이름을 바꾸거나 폴더를 옮겨도 사용 이력이 유지된다.

네이버는 유사문서를 판정할 때 사진도 비교한다 (이미지 1장 = 비교 단위 1개).
크롭·색보정으로는 같은 사진임을 숨길 수 없다고 보고, 대신 같은 사진을 자주 쓰지 않도록
이 모듈이 재사용을 제한한다.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image
from pydantic import BaseModel, Field

from blogbot import llm


class PhotoDescription(BaseModel):
    description: str = Field(description="사진에 무엇이 보이는지 1~2문장. 보이는 것만, 추측 없이")
    keywords: list[str] = Field(description="이 사진이 어울리는 주제 키워드 3~6개")
    specific_details: list[str] = Field(
        description="장소·상황을 알려주는 구체적 단서 (간판, 메뉴, 손, 소품, 날씨, 시간대 등). 없으면 빈 목록"
    )
    orientation: str = Field(description="가로 / 세로 / 정사각형")


def file_hash(path: Path) -> str:
    h = hashlib.sha1()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def load_json(path: Path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def taken_at(path: Path) -> str | None:
    """EXIF 촬영 시각 (업로드용 사진에서는 지우지만, 최신성 판단을 위해 인덱스에만 기록)."""
    try:
        with Image.open(path) as im:
            exif = im.getexif()
            value = exif.get_ifd(0x8769).get(0x9003) or exif.get(0x0132)
        if value:
            return datetime.strptime(str(value)[:19], "%Y:%m:%d %H:%M:%S").isoformat()
    except Exception:
        pass
    return None


def index_photos(photos: list[Path], index_path: Path, model: str, source: str) -> dict:
    """새로 추가된 사진만 Claude 비전으로 설명을 붙여 인덱스에 저장한다.

    source: "archive" (예전에 이미 블로그에 올렸던 사진) / "new" (아직 어디에도 안 올린 사진)
    """
    index: dict = load_json(index_path, {})
    for p in photos:
        h = file_hash(p)
        entry = index.get(h)
        if entry is None:
            print(f"  사진 분석: {p.name}")
            desc = llm.parse(
                model=model,
                system="블로그에 쓸 사진을 분류하는 도우미입니다. 한국어로 간결하게 답하세요.",
                content=[llm.image_block(p), {"type": "text", "text": "이 사진을 설명하세요."}],
                output=PhotoDescription,
                effort="low",
                max_tokens=8000,
            )
            with Image.open(p) as im:
                w, h_px = im.size
            entry = {
                **desc.model_dump(),
                "taken_at": taken_at(p),
                "size": [w, h_px],
                "source": source,
                "paths": [],
            }
            index[h] = entry
        path_str = str(p.resolve())
        if path_str not in entry["paths"]:
            entry["paths"].append(path_str)
        # 새 사진 폴더로 들어왔더라도 예전 사진과 같은 파일이면 archive 로 유지
        if entry.get("source") != "archive":
            entry["source"] = source
        save_json(index_path, index)
    return index


def existing_path(entry: dict) -> Path | None:
    for p in entry.get("paths", []):
        if Path(p).exists():
            return Path(p)
    return None


class Ledger:
    """사진 사용 이력과 발행 기록.

    - 같은 사진의 과도한 재사용을 막는다 (유사문서 위험).
    - 발행 간격·일/주 발행량을 제한한다 (대량 발행은 어뷰징 패턴).
    - 검색 누락이 연속되면 발행을 멈춘다 (circuit breaker).
    """

    def __init__(self, path: Path):
        self.path = path
        self.data = load_json(path, {})
        self.data.setdefault("photo_uses", {})
        self.data.setdefault("posts", [])
        self.data.setdefault("halted", "")

    def save(self) -> None:
        save_json(self.path, self.data)

    # -- 사진 ----------------------------------------------------------------
    def use_count(self, photo_id: str, entry: dict | None = None, archive_counts_as_used: bool = True) -> int:
        n = len(self.data["photo_uses"].get(photo_id, []))
        if archive_counts_as_used and entry and entry.get("source") == "archive":
            n += 1
        return n

    def photo_available(
        self, photo_id: str, entry: dict, cooldown_days: int, max_uses: int, archive_counts_as_used: bool = True
    ) -> bool:
        if self.use_count(photo_id, entry, archive_counts_as_used) >= max_uses:
            return False
        uses = self.data["photo_uses"].get(photo_id, [])
        if uses:
            last = datetime.fromisoformat(uses[-1]["at"])
            if datetime.now() - last < timedelta(days=cooldown_days):
                return False
        return True

    def record_photos(self, photo_ids: list[str], draft: str) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        for pid in photo_ids:
            self.data["photo_uses"].setdefault(pid, []).append({"at": now, "draft": draft})

    # -- 발행 ----------------------------------------------------------------
    def _post_times(self) -> list[datetime]:
        return [
            datetime.fromisoformat(p["at"])
            for p in self.data["posts"]
            if p.get("status") in ("published", "unknown", "saved_to_naver_draft")
        ]

    def schedule_block_reason(self, sched: dict, now: datetime | None = None) -> str:
        """발행하면 안 되는 이유 (없으면 빈 문자열). 기준은 24시간/7일 롤링."""
        now = now or datetime.now()
        if self.data.get("halted"):
            return f"발행 중단 상태입니다: {self.data['halted']} (`python -m blogbot check` 참고)"
        times = self._post_times()
        per_day = int(sched.get("max_posts_per_day", 1))
        per_week = int(sched.get("max_posts_per_week", 4))
        gap = float(sched.get("min_interval_hours", 20))
        if times and now - max(times) < timedelta(hours=gap):
            wait = max(times) + timedelta(hours=gap) - now
            return f"직전 발행 후 {gap:g}시간이 지나지 않았습니다 (약 {wait.seconds // 3600}시간 {wait.seconds % 3600 // 60}분 남음)."
        if sum(now - t < timedelta(hours=24) for t in times) >= per_day:
            return f"최근 24시간 발행 한도({per_day}건)에 도달했습니다."
        if sum(now - t < timedelta(days=7) for t in times) >= per_week:
            return f"최근 7일 발행 한도({per_week}건)에 도달했습니다."
        return ""

    def record_post(self, **fields) -> None:
        fields.setdefault("at", datetime.now().isoformat(timespec="seconds"))
        self.data["posts"].append(fields)

    def published_titles(self) -> list[str]:
        return [p.get("title", "") for p in self.data["posts"] if p.get("status") == "published"]
