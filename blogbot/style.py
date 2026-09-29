"""과거 원고에서 말투·구성·흐름을 뽑아 문체 프로필을 만든다."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

from blogbot import llm
from blogbot.corpus import Manuscript


class Evidence(BaseModel):
    trait: str = Field(description="특징 이름")
    quotes: list[str] = Field(description="그 특징이 드러나는 원고 속 실제 문장 1~3개 (그대로 인용)")


class StyleProfile(BaseModel):
    tone: str = Field(description="말투와 분위기")
    sentence_endings: list[str] = Field(description="자주 쓰는 문장 끝맺음, 빈도 순, 각 항목에 대략 비율")
    self_reference: str = Field(description="글쓴이가 자신을 부르는 방식")
    reader_address: str = Field(description="독자를 부르는 호칭, 말 거는 빈도와 형태(질문·권유), 반말/존댓말 전환")
    vocabulary: str = Field(description="어휘 수준, 한자어·외래어·신조어 사용, 강조 부사와 형용사 습관, 전문용어 정도")
    sentence_rhythm: str = Field(description="짧은 문장과 긴 문장의 교차, 한 문장 한 줄 여부, 쉼표·중간 줄바꿈 습관")
    humor_and_emotion: str = Field(description="유머 방식, 감탄사, ㅎㅎ/ㅋㅋ 표기, 감정 강도, 과장 정도")
    information_density: str = Field(description="문단당 사실의 양, 설명 대 감상 비율")
    experience_narration: str = Field(
        description="경험을 풀어가는 방식: 시간순인지 결론 먼저인지, 감각 묘사 정도, 날씨·동행·시간대 언급, 단점·실패담을 얼마나 솔직하게 쓰는지"
    )
    numbers_and_names: str = Field(description="가격·시간·수량을 적는 빈도와 표기, 지명·브랜드 표기 방식, 단위 표기")
    punctuation: str = Field(description="~ … ! 괄호 덧붙임 습관, 의도적인 맞춤법·띄어쓰기 특징")
    emoji_and_symbols: str = Field(description="이모티콘/특수문자 사용 습관")
    paragraph_style: str = Field(description="문단 길이, 줄바꿈, 빈 줄, 한 문단당 문장 수")
    layout: str = Field(description="소제목·인용구·구분선 사용 여부와 방식")
    structure: list[str] = Field(description="글의 전형적인 흐름을 단계별로")
    opening_patterns: list[str] = Field(description="글을 시작하는 전형적인 방식")
    closing_patterns: list[str] = Field(description="글을 마무리하는 전형적인 방식")
    signature_phrases: list[str] = Field(description="매번 거의 그대로 쓰는 인사말·마무리 문구 (원문 그대로)")
    product_mention_style: str = Field(description="제품/서비스를 언급하고 권유하는 방식")
    photo_usage: str = Field(description="사진 앞뒤 문장 습관. [사진] 표식이 없으면 '원고에서 확인 불가'")
    caption_style: str = Field(description="사진 설명 문장 형식. 확인할 수 없으면 '원고에서 확인 불가'")
    title_patterns: list[str] = Field(description="제목 짓는 패턴과 실제 제목 예시")
    topics: list[str] = Field(description="이 블로그가 꾸준히 다루는 주제군 2~5개")
    recent_shift: str = Field(description="최근 글에서 달라진 점. 없으면 '없음'")
    dos: list[str] = Field(description="이 사람처럼 쓰기 위해 지킬 것")
    donts: list[str] = Field(description="이 사람이 쓰지 않는 표현/방식, 그리고 따라 하면 안 되는 나쁜 습관")
    evidence: list[Evidence] = Field(description="위 특징들 중 핵심 6~12개의 근거 인용")


SYSTEM = """당신은 한국어 블로그 글의 문체를 분석하는 편집자입니다.
주어진 원고들은 모두 한 사람이 쓴 네이버 블로그 글입니다 (오래된 순으로 정렬).
다른 작가가 이 프로필만 보고도 같은 사람이 쓴 것처럼 새 글을 쓸 수 있도록,
말투·어휘·문장 리듬·경험을 풀어가는 방식·글의 흐름을 정리하세요.

- 원고에 실제로 나타난 특징만 적습니다. 일반적인 블로그 작성 요령은 넣지 않습니다.
- 여러 편에서 반복되는 특징만 '습관'으로 적고, 한두 편에만 나오면 '가끔'이라고 표시합니다.
- 문체가 시기에 따라 달라졌다면 최근 글의 특징을 우선하고, 달라진 점은 recent_shift 에 적습니다.
- 원고에 [사진] 표식이 있으면 사진 앞뒤 문장 습관을 분석하고, 없으면 사진 관련 항목은 '원고에서 확인 불가'로 둡니다.
- 키워드를 기계적으로 반복하기, 근거 없는 효능·최상급 표현, 이웃추가·공감·댓글 요구, 매 글 똑같은 홍보 문단 같은
  검색 노출에 불리한 습관은 dos 가 아니라 donts 에 '따라 하지 말 것'으로 적습니다."""


def build_profile(
    docs: list[Manuscript], model: str, effort: str, max_docs: int = 60
) -> StyleProfile:
    if not docs:
        raise SystemExit("data/manuscripts 에 원고가 없습니다. (.txt / .md / .docx)")
    ordered = sorted(docs, key=lambda d: d.date or 0)
    if len(ordered) > max_docs:
        print(f"  원고 {len(ordered)}편 중 최근 {max_docs}편으로 문체를 분석합니다 (통계는 전체 사용).")
        ordered = ordered[-max_docs:]
    body = "\n\n".join(
        f'<manuscript name="{d.name}" date="{d.date:%Y-%m-%d}">\n{d.text}\n</manuscript>'
        for d in ordered
    )
    return llm.parse(
        model=model,
        system=SYSTEM,
        content=f"{body}\n\n위 원고 {len(ordered)}편의 문체 프로필을 작성하세요.",
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
