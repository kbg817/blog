"""과거 원고에서 말투·구성·흐름을 뽑아 문체 프로필을 만든다."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

from blogbot import llm
from blogbot.corpus import Manuscript


class StyleProfile(BaseModel):
    tone: str = Field(description="전반적인 말투와 분위기 (예: 친근한 존댓말, 수다스러움)")
    sentence_endings: list[str] = Field(description="자주 쓰는 문장 끝맺음 (예: ~했어요, ~더라구요)")
    self_reference: str = Field(description="글쓴이가 자신을 부르는 방식과 독자를 부르는 방식")
    signature_phrases: list[str] = Field(description="반복적으로 쓰는 표현, 인사말, 감탄사")
    emoji_and_symbols: str = Field(description="이모티콘/특수문자/ㅎㅎ 같은 표현 사용 습관")
    paragraph_style: str = Field(description="문단 길이, 줄바꿈 습관, 한 문단당 문장 수")
    structure: list[str] = Field(description="글의 전형적인 흐름을 단계별로 (도입→...→마무리)")
    opening_patterns: list[str] = Field(description="글을 시작하는 전형적인 방식")
    closing_patterns: list[str] = Field(description="글을 마무리하는 전형적인 방식")
    product_mention_style: str = Field(description="제품/서비스를 언급하고 권유하는 방식")
    photo_usage: str = Field(description="사진을 어디에 어떻게 배치하고 사진 앞뒤로 어떤 문장을 쓰는지")
    title_patterns: list[str] = Field(description="제목을 짓는 패턴과 실제 예시")
    dos: list[str] = Field(description="이 사람처럼 쓰기 위해 반드시 지킬 것")
    donts: list[str] = Field(description="이 사람이 쓰지 않는 표현/방식")


SYSTEM = """당신은 한국어 블로그 글의 문체를 분석하는 편집자입니다.
주어진 원고들은 모두 한 사람이 쓴 네이버 블로그 글입니다.
다른 작가가 이 프로필만 보고도 같은 사람이 쓴 것처럼 새 글을 쓸 수 있도록,
말투·어휘·문장 리듬·글의 흐름을 구체적인 예시 문장과 함께 정리하세요.
원고에 실제로 나타난 특징만 적고, 일반적인 블로그 작성 요령은 넣지 마세요."""


def build_profile(docs: list[Manuscript], model: str, effort: str) -> StyleProfile:
    if not docs:
        raise SystemExit("data/manuscripts 에 원고가 없습니다. (.txt / .md / .docx)")
    body = "\n\n".join(
        f'<manuscript name="{d.name}">\n{d.text}\n</manuscript>' for d in docs
    )
    return llm.parse(
        model=model,
        system=SYSTEM,
        content=f"{body}\n\n위 원고 {len(docs)}편의 문체 프로필을 작성하세요.",
        output=StyleProfile,
        effort=effort,
    )


def save_profile(profile: StyleProfile, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(profile.model_dump_json(indent=2), encoding="utf-8")


def load_profile(path: Path) -> StyleProfile:
    if not path.exists():
        raise SystemExit("문체 프로필이 없습니다. 먼저 `python -m blogbot learn` 을 실행하세요.")
    return StyleProfile.model_validate(json.loads(path.read_text(encoding="utf-8")))
