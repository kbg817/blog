"""문체 프로필 + 예시 원고 + 사업 정보 + 이번 글의 메모로 새 글을 쓰고, 검사하고, 다듬는다.

설계 근거 (네이버 공식 자료 기준):
- C-Rank(출처 단위: 주제 집중도·품질·반응) + D.I.A.+(문서 단위: 주제 적합도·경험 정보·
  충실성·의도·독창성·적시성·어뷰징 척도) + 유사문서 판독이 현행 체계 (2025-02-28 재확인).
- 2026-05-26 네이버 검색 가이드: 유리한 글 = 직접 경험 / 일관된 주제 / 진정성 /
  읽기 쉬운 구조 / 최신성. 불리한 글 = 반복 키워드 / 낚시성 제목 / 짜깁기 /
  기계적 AI 생성 / 과도한 홍보. AI 도구 사용 자체는 불이익이 아님.
- 글자수·키워드 횟수·제목 길이의 공식 기준은 없다. 그래서 수치 검사는 모두 '경고'이고,
  발행을 막는 것은 사실 확인·유사문서·참여 유도처럼 명확히 불리한 항목뿐이다.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from blogbot import llm
from blogbot.corpus import Manuscript, overlap_report, pick_examples, similarity, style_drift
from blogbot.style import StyleProfile

# ---- 스키마 -------------------------------------------------------------


class Block(BaseModel):
    kind: Literal["heading", "text", "image", "quote", "divider"]
    text: str = Field(
        description=(
            "heading: 소제목 한 줄. text: 문단 (문장이 끝나면 줄바꿈 \\n). quote: 인용구로 강조할 1~2문장. "
            "image: 사진 바로 아래 들어갈 자연스러운 한 문장 (무엇을 찍었고 어땠는지, 키워드 나열 금지). "
            "divider: 빈 문자열. 마크다운·HTML 기호(**, #, -, >)는 쓰지 않는다."
        )
    )
    photo: str = Field(description="image 일 때 사진 목록의 ID (예: P3). 그 외에는 빈 문자열")


class Claim(BaseModel):
    text: str = Field(description="본문에 쓴 구체적 정보·경험 (숫자, 가격, 시간, 효능, 고유명사, 직접 겪은 일)")
    source: Literal["business", "notes", "general_knowledge", "example_manuscript", "none"] = Field(
        description="그 정보의 출처. 사업 정보=business, 이번 메모=notes, 누구나 아는 상식=general_knowledge, "
        "예시 원고=example_manuscript, 근거 없음=none"
    )


class Post(BaseModel):
    search_intent: str = Field(description="이 키워드로 검색한 사람이 알고 싶은 것 1~2문장 (쓰기 전에 먼저 정리)")
    new_info: list[str] = Field(description="이 글에만 있는 새 정보·경험. 반드시 <notes> 에서 가져온 것만")
    title: str = Field(description="글 내용을 정확히 대표하는 제목. 주 키워드를 자연스럽게 포함, 낚시성 표현 없이")
    blocks: list[Block]
    tags: list[str] = Field(description="태그 5~10개, # 없이. 첫 번째는 주 키워드, 나머지는 본문에 실제로 나온 연관어")
    claims: list[Claim] = Field(description="본문의 구체적 정보·경험을 빠짐없이 나열하고 출처를 표시")


class Edit(BaseModel):
    block_index: int
    new_text: str


class Revision(BaseModel):
    title: str = Field(description="제목을 고쳐야 하면 새 제목, 아니면 빈 문자열")
    edits: list[Edit] = Field(description="고친 블록만. 지적되지 않은 블록은 넣지 않는다")


# ---- 기본 목록 -----------------------------------------------------------

DEFAULT_AI_TELLS = [
    "에 대해 알아보", "알아보겠습니다", "살펴보겠습니다", "소개해 드리겠습니다", "결론적으로", "요약하자면",
    "정리하자면", "마무리하며", "것이 중요합니다", "것이 좋습니다", "라고 할 수 있습니다", "되어집니다",
    "첫째,", "둘째,", "셋째,", "다양한", "혁신적", "완벽한", "최고의", "여러분의", "를 통해",
    "도움이 되셨길", "궁금증이 해결", "지금 바로",
]
DEFAULT_LEGAL_WATCH = [
    "치료", "완치", "효능", "효과 보장", "부작용 없", "100%", "최초", "1위", "유일", "최저가", "무조건", "보장합니다",
]
CLICKBAIT = ["완벽", "총정리", "충격", "역대급", "꼭 보세요", "무조건", "대박", "필독", "모르면 손해"]
# 공감·댓글이라는 단어 자체가 아니라 '요청'과 '대가 약속'만 잡는다
# ("많은 분들이 공감해 주셨어요", "댓글 주시면 답변 드려요" 는 괜찮음)
ENGAGEMENT_BAIT = re.compile(
    r"이웃\s*추가|서로\s*이웃|서이추|공감\s*(버튼|꾹)|공감.{0,4}(눌러|부탁)|스크랩.{0,6}부탁|"
    r"댓글.{0,15}(증정|추첨|이벤트|선물)"
)
NEEDS_CHECK = re.compile(r"[\[\(【]\s*확인\s*필요[^\]\)】]*[\]\)】]")
URL_RE = re.compile(r"https?://\S+|www\.\S+")
NUM_RE = re.compile(r"\d[\d,.]*\s*(?:원|만원|천원|%|분|시간|일|개월|년|개|명|km|m|g|kg|ml|L|평|회|잔|인분)")
MARKDOWN_RE = re.compile(r"\*\*|__|^\s*#{1,6}\s+|^\s*>\s+|^\s*[-*•]\s+|^\s*(?:✅|✔️|✔|👉|▶|►|☑️|🔹|🔸)\s*", re.M)

KIND_GUIDE = {
    "experience": "경험 글: 내가 직접 겪은 일을 시간 흐름대로. 감각 묘사와 판단은 <notes> 에 있는 것만.",
    "info": "정보 글: 검색한 사람의 질문에 절차·비교·기준·주의점으로 답한다. 경험은 <notes> 에 있으면 근거로 곁들인다.",
    "promo": "소개 글: 글의 대부분은 읽는 사람에게 쓸모 있는 정보이고, 제품·서비스 이야기는 그 흐름 안에서 자연스럽게.",
}

# ---- 프롬프트 -----------------------------------------------------------

SYSTEM_TEMPLATE = """당신은 아래 문체 프로필의 주인공 본인입니다. 자기 네이버 블로그에 새 글을 씁니다.

<style_profile>
{profile}
</style_profile>

<style_stats>
원고에서 코드로 계산한 수치입니다. 새 글도 이 범위 안에 있어야 같은 사람 글처럼 읽힙니다.
{stats}
</style_stats>

<business>
{business}
</business>"""

RULES = """목표: 이 사람이 오늘 새로 겪은 일을 직접 쓴 것처럼 읽히고, 이 키워드로 검색한 사람이 답을 얻고 나가는 글.

네이버가 공식적으로 밝힌 '선택받는 글'의 기준을 지킵니다:
1. 직접 경험: 이 글만의 새 정보는 <notes> 에서 옵니다. 언제·어디서·무엇을·얼마나·어땠는지를 <notes> 에 있는 만큼 구체적으로 씁니다.
2. 일관된 주제: 블로그 주제({topics}) 안에서, 한 글은 검색 의도 하나에만 답합니다.
3. 진정성: 과장하지 않고, 단점이나 주의점이 <notes> 에 있으면 숨기지 않습니다.
4. 읽기 쉬운 구조: 첫 문단에서 검색한 사람이 궁금한 것에 먼저 답합니다 (인사말은 프로필 습관대로 짧게). 내용이 길면 소제목으로 나누고, 소제목 바로 아래 문단은 그 소제목만 읽어도 이해되게 씁니다.
5. 최신성: 오늘은 {today} 입니다. 가격·영업시간·행사처럼 바뀌는 정보 옆에는 기준 시점을 한 번 적습니다.

사실과 경험 규칙 (가장 중요):
- 숫자·가격·기간·경력·효능·순위·고유명사는 <business> 와 <notes> 에 있는 것만 씁니다.
- 맛·향·촉감 같은 감각 묘사, 에피소드, 손님 반응, "제가 해보니" 같은 경험도 <notes> 에 있는 것만 씁니다. 없으면 지어내지 말고 정보 설명으로 대신하거나 [확인필요: 무엇이 필요한지] 를 남깁니다.
- <examples> 의 내용은 모두 과거의 것입니다. 그 속의 가격·메뉴·행사·에피소드를 이번 글의 사실로 쓰지 않습니다.
- 치료·완치·효능 보장, 최고·최초·1위·유일 같은 단정 표현은 <business> 에 근거가 적혀 있을 때만 씁니다.
- claims 에는 본문에 쓴 구체적 정보를 빠짐없이 적고 출처를 정직하게 표시합니다.

예시 원고에서 가져올 것: 호칭, 끝맺음, 문장 길이와 줄바꿈 리듬, 연결어, 도입과 마무리 방식, 사진 앞뒤 문장 습관.
가져오지 말 것: 문장 자체, 사실, 에피소드, 글의 순서. 같은 사람이 새 경험을 처음부터 다시 쓴 글이어야 합니다.
(예) 원고: "오늘은 새로 들어온 에티오피아 원두 이야기를 해볼게요. 산미가 살짝 있고 꽃향이 나요."
     새 주제 '밤라떼 출시' → "오늘은 이번 주에 처음 내놓은 밤라떼 얘기를 좀 해볼게요. 첫 모금은 고소하고 끝은 은근히 달아요."
     도입 습관과 짧은 감각 문장 리듬만 같고, 단어·사실·문장은 전부 새로 썼습니다. (감각 묘사는 notes 에 있을 때만)
프로필의 signature_phrases 는 늘 쓰는 인사말이라 그대로 써도 됩니다.

형식:
- 제목: 주 키워드를 자연스럽게 넣고, 글 내용을 정확히 대표하게. 낚시성 표현, 특수문자·이모지로 시작하기, 제목에 [확인필요] 금지. 프로필의 제목 패턴을 따릅니다.
- 주 키워드는 제목과 첫 문단에 자연스럽게 한 번씩, 본문에서는 문장이 필요로 하는 자리에만. 키워드를 채우려고 반복하지 않습니다 (네이버가 명시한 어뷰징).
- 사진: 제공된 목록에서 문단 내용과 실제로 맞는 사진만, 목록의 ID 로. 맞는 사진이 없으면 넣지 않습니다. 첫 사진은 피드 썸네일이 되므로 글의 핵심이 한눈에 보이는 사진으로. 사진은 두 장까지만 연속으로 두고 그 뒤에는 글을 씁니다. "재사용"으로 표시된 사진은 한 장까지만.
- 블로그 전체에 URL 을 쓰지 않습니다. 이웃추가·공감·댓글을 요구하거나 대가를 약속하는 문장을 쓰지 않습니다.
- 마무리 안내는 글 끝에 한 번, 매번 조금씩 다른 말로. 가게 이름을 여러 번 반복하지 않습니다.
- 마크다운 기호, 번호 매긴 나열, 이모지 불릿, 요약·정리 문단, "~에 대해 알아보겠습니다", "결론적으로", "~하는 것이 중요합니다" 같은 AI 티 나는 표현을 쓰지 않습니다. 문단 길이는 원고처럼 들쭉날쭉해도 됩니다.
- 인용구(quote)와 구분선(divider)은 프로필의 layout 에 그런 습관이 있을 때만, 그 습관만큼 씁니다. 매 글 같은 서식 패턴을 반복하지 않습니다.
- 글쓴이는 이 사업의 운영자/직원입니다 (<business> 의 관계 참고). 손님이나 제3자의 후기처럼 쓰지 않고, 운영하는 사람의 시점이 드러나게 씁니다.

<avoid>
{avoid}
</avoid>"""


def _business_text(biz: dict) -> str:
    facts = "\n".join(f"- {f}" for f in biz.get("facts", []) or []) or "(없음)"
    return (
        f"이름: {biz.get('name') or ''}\n"
        f"글쓴이와의 관계: {biz.get('relationship', 'owner')}\n"
        f"소개: {(biz.get('description') or '').strip()}\n"
        f"블로그 주제: {', '.join(biz.get('topics', []) or [])}\n"
        f"확정 정보 (글에 써도 되는 사실은 이것뿐):\n{facts}\n"
        f"마무리 안내 (뜻은 유지하되 매번 다른 말로): {biz.get('call_to_action') or ''}"
    )


def _examples_text(examples: list[tuple[Manuscript, str]]) -> str:
    return "\n\n".join(
        f'<example name="{d.name}" date="{d.date:%Y-%m-%d}" use="{role}">\n{d.text}\n</example>'
        for d, role in examples
    )


def _photos_text(photo_ids: dict[str, dict], reused: set[str]) -> str:
    if not photo_ids:
        return "(사용 가능한 사진 없음 — image 블록을 쓰지 마세요)"
    lines = []
    for pid, e in photo_ids.items():
        extra = []
        if e.get("taken_at"):
            extra.append(f"촬영 {e['taken_at'][:10]}")
        if e.get("orientation"):
            extra.append(e["orientation"])
        if pid in reused:
            extra.append("재사용")
        details = ", ".join(e.get("specific_details", []) or [])
        lines.append(
            f"- {pid}: {e.get('description', '')}"
            + (f" / 단서: {details}" if details else "")
            + (f" ({'; '.join(extra)})" if extra else "")
        )
    return "\n".join(lines)


def write_post(
    *,
    topic: str,
    keyword: str,
    sub_keywords: list[str],
    notes: str,
    kind: str,
    profile: StyleProfile,
    stats: dict,
    docs: list[Manuscript],
    photo_ids: dict[str, dict],
    reused_ids: set[str],
    recent_posts: list[dict],
    cfg: dict,
) -> Post:
    wcfg = cfg.get("writing", {})
    biz = cfg.get("business", {})
    examples = pick_examples(f"{topic} {keyword} {' '.join(sub_keywords)}", docs, int(wcfg.get("examples_per_prompt", 3)))
    avoid = list(dict.fromkeys(DEFAULT_AI_TELLS + list(wcfg.get("banned_phrases", []) or []) + profile.donts))
    recent = "\n".join(
        f"- {p['title']} / 소제목: {', '.join(p.get('headings', [])) or '없음'}" for p in recent_posts[-10:]
    ) or "(없음)"

    system = SYSTEM_TEMPLATE.format(
        profile=profile.model_dump_json(indent=2),
        stats=json.dumps(stats, ensure_ascii=False, indent=2),
        business=_business_text(biz),
    )
    rules = RULES.format(
        topics=", ".join(biz.get("topics", []) or profile.topics) or "기존 블로그 주제",
        today=datetime.now().strftime("%Y년 %m월 %d일"),
        avoid="\n".join(f"- {a}" for a in avoid),
    )
    length = wcfg.get("length_no_space", {}) or {}
    user = f"""{rules}

<examples>
{_examples_text(examples)}
</examples>

<photos>
{_photos_text(photo_ids, reused_ids)}
</photos>

<recent_posts>
최근에 올린 글입니다. 제목·소제목·구성이 겹치지 않게 씁니다.
{recent}
</recent_posts>

<notes>
{notes.strip() or '(없음 — 경험·감각 묘사는 쓰지 말고 필요한 곳은 [확인필요] 로 남기세요)'}
</notes>

이번 글:
- 주제: {topic}
- 주 키워드: {keyword}
- 보조 키워드: {', '.join(sub_keywords) or '(없음)'}
- 글 종류: {KIND_GUIDE.get(kind, KIND_GUIDE['experience'])}
- 분량: 공백 제외 약 {length.get("target", 1500)}자. 채우려고 늘리지 말고, 할 말이 적으면 짧게.
- 사진: {wcfg.get('images_per_post', 5)}장 이하"""

    post = llm.parse(
        model=wcfg.get("model", "claude-opus-5-5"),
        system=system,
        content=user,
        output=Post,
        effort=wcfg.get("effort", "high"),
    )
    return clean_post(post, keyword, photo_ids, reused_ids, int(cfg.get("images", {}).get("max_reused_per_post", 1)))


# ---- 후처리 --------------------------------------------------------------


def normalize_tags(tags: list[str], keyword: str, limit: int = 10) -> list[str]:
    out = []
    for t in [keyword] + list(tags):
        t = re.sub(r"[\s#,]+", "", t or "")
        if t and t not in out:
            out.append(t)
    return out[:limit]


def clean_post(post: Post, keyword: str, photo_ids: dict, reused_ids: set[str], max_reused: int) -> Post:
    seen: set[str] = set()
    reused_used = 0
    blocks = []
    for b in post.blocks:
        if b.kind == "image":
            if b.photo not in photo_ids or b.photo in seen:
                continue
            if b.photo in reused_ids:
                if reused_used >= max_reused:
                    continue
                reused_used += 1
            seen.add(b.photo)
        elif b.kind == "divider":
            b.text = ""
        else:
            b.text = MARKDOWN_RE.sub("", b.text).strip()
            if not b.text:
                continue
        blocks.append(b)
    post.blocks = blocks
    post.title = MARKDOWN_RE.sub("", post.title).strip()
    post.tags = normalize_tags(post.tags, keyword)
    return post


def body_text(post: Post) -> str:
    """본문 (소제목·문단·인용구). 사진 설명 제외."""
    return "\n".join(b.text for b in post.blocks if b.kind in ("heading", "text", "quote"))


def all_text(post: Post) -> str:
    return "\n".join([post.title] + [b.text for b in post.blocks] + post.tags)


def chars_no_space(text: str) -> int:
    return len(re.sub(r"\s", "", text))


# ---- 검사 ---------------------------------------------------------------


def lint_post(post: Post, ctx: dict) -> list[dict]:
    """발행 전 검사. level=block 은 발행을 막고, warn 은 preview 에 표시만 한다."""
    L: list[dict] = []

    def add(level, code, msg):
        L.append({"level": level, "code": code, "msg": msg})

    cfg = ctx["cfg"]
    wcfg, biz = cfg.get("writing", {}), cfg.get("business", {})
    kw = ctx["keyword"]
    body = body_text(post)
    everything = all_text(post)
    n_chars = chars_no_space(body)
    kw_ns = re.sub(r"\s", "", kw)

    # --- 발행을 막는 항목 ---
    for m in sorted(set(NEEDS_CHECK.findall(everything))):
        add("block", "needs_check", f"직접 채워야 할 부분: {m}")
    for c in post.claims:
        if c.source in ("none", "example_manuscript"):
            add("block", "unsourced_claim", f"출처 없는 정보 ({c.source}): {c.text}")
    m = ENGAGEMENT_BAIT.search(everything)
    if m:
        add("block", "engagement_bait", f"이웃추가·공감·댓글 유도 문구 (네이버가 어뷰징으로 분류): '{m.group(0)}'")
    urls = URL_RE.findall(everything)
    if len(urls) >= 2:
        add("block", "urls", f"URL {len(urls)}개 (외부 링크는 0~1개 권장)")
    ov = ctx["overlap"]
    if ov["max_ratio"] > float(wcfg.get("max_overlap_ratio", 0.15)):
        add("block", "overlap", f"'{ov['most_similar']}' 와 {ov['max_ratio']:.0%} 겹침 (유사문서 위험)")
    for s in ov["copied_sentences"][:5]:
        add("warn", "copied_sentence", f"과거 글과 거의 같은 문장: {s}")
    for prev_title in ctx.get("previous_titles", []):
        if prev_title and similarity(post.title, prev_title) >= 0.85:
            add("block", "title_repeat", f"예전 글 제목과 거의 같음: '{prev_title}'")
            break
    if not post.blocks or n_chars < 200:
        add("block", "too_short", f"본문이 너무 짧습니다 ({n_chars}자)")

    # --- 경고 ---
    t = post.title
    if not (10 <= len(t) <= 45):
        add("warn", "title_length", f"제목 {len(t)}자 (10~45자 권장, 상위 글은 대략 25~45자)")
    if re.match(r"^[^\w가-힣]", t):
        add("warn", "title_symbol", "제목이 특수문자/이모지로 시작합니다")
    bait = [w for w in CLICKBAIT if w in t]
    if bait:
        add("warn", "clickbait", f"낚시성으로 보일 수 있는 제목 표현: {', '.join(bait)}")
    if kw_ns not in re.sub(r"\s", "", t):
        add("warn", "kw_title", f"제목에 주 키워드 '{kw}' 가 없습니다")
    first_texts = " ".join(b.text for b in post.blocks if b.kind == "text")[:200]
    if kw_ns not in re.sub(r"\s", "", first_texts):
        add("warn", "kw_intro", "첫 문단(앞 200자)에 주 키워드가 없습니다")
    kw_count = re.sub(r"\s", "", body).count(kw_ns) if kw_ns else 0
    # 공식 기준은 없음. 실무에서는 1,500~2,000자에 8회 이상이면 과하다고 봄 → 1,000자당 5회 또는 8회 중 큰 값
    if kw_count > max(8, round(n_chars * 5 / 1000)):
        add("warn", "kw_stuffing", f"주 키워드 {kw_count}회 — 반복이 많습니다 (키워드 반복은 네이버 명시 어뷰징)")
    length = wcfg.get("length_no_space", {}) or {}
    if n_chars < int(length.get("min", 900)):
        add("warn", "length", f"본문 {n_chars}자 (공백 제외). 내용이 충분한지 확인")
    elif n_chars > int(length.get("max", 4000)):
        add("warn", "length", f"본문 {n_chars}자 (공백 제외). 늘어진 부분이 없는지 확인")
    if n_chars > 1500 and not any(b.kind == "heading" for b in post.blocks):
        add("warn", "no_headings", "긴 글인데 소제목이 없습니다 (읽기 쉬운 구조)")
    long_paras = sum(1 for b in post.blocks if b.kind == "text" and chars_no_space(b.text) > 250)
    if long_paras:
        add("warn", "long_paragraph", f"긴 문단 {long_paras}개 (모바일 가독성)")
    images = [b for b in post.blocks if b.kind == "image"]
    empty_caps = sum(1 for b in images if not b.text.strip())
    if empty_caps:
        add("warn", "empty_caption", f"사진 설명이 빈 사진 {empty_caps}장")
    run = best = 0
    for b in post.blocks:
        run = run + 1 if b.kind == "image" else 0
        best = max(best, run)
    if best >= 3:
        add("warn", "image_run", f"사진이 {best}장 연속입니다")
    if len(images) < int(wcfg.get("min_images", 3)):
        add("warn", "few_images", f"사진 {len(images)}장 — 직접 찍은 새 사진을 더 넣는 것을 권장")
    if ctx.get("reused_in_post"):
        add("warn", "reused_photo", f"예전에 쓴 사진 {ctx['reused_in_post']}장 포함 (유사문서는 사진도 비교)")
    stale = ctx.get("stale_photos", 0)
    if stale:
        add("warn", "old_photos", f"1년 넘은 사진 {stale}장 — 최신 정보처럼 쓰였는지 확인")
    content_ns = re.sub(r"\s", "", "\n".join([post.title] + [b.text for b in post.blocks]))
    unrelated = [x for x in post.tags[1:] if re.sub(r"\s", "", x) not in content_ns]
    if unrelated:
        add("warn", "tags", f"본문에 안 나오는 태그: {', '.join(unrelated)}")
    if not re.search(r"20\d{2}\s*년|\d{1,2}\s*월", body):
        add("warn", "freshness", "기준 시점(예: 2026년 9월 기준) 표기가 없습니다")
    name = (biz.get("name") or "").strip()
    if name and body.count(name) > 5:
        add("warn", "brand_repeat", f"가게/브랜드 이름 {body.count(name)}회 (과도한 홍보로 보일 수 있음)")
    signatures = set(ctx.get("signatures", []))
    tells = sorted({p for p in ctx["ai_tells"] if p in body and not any(p in s for s in signatures)})
    if tells:
        add("warn", "ai_tell", f"AI 티 나는 표현: {', '.join(tells)}")
    legal = sorted({w for w in ctx["legal_watch"] if w in everything})
    if legal:
        add("warn", "legal", f"표시광고 관련 확인 권장 표현: {', '.join(legal)}")
    for c in post.claims:
        if c.source == "general_knowledge":
            add("warn", "general_claim", f"상식으로 쓴 정보 — 사실인지 확인: {c.text}")
    known = re.sub(r"\s", "", ctx.get("known_facts", ""))
    for num in sorted(set(NUM_RE.findall(body))):
        if re.fullmatch(r"20\d{2}\s*년", num):  # 기준 시점 표기는 권장 사항이므로 제외
            continue
        if re.sub(r"\s", "", num) not in known:
            add("warn", "number", f"메모/사업 정보에 없는 수치: {num}")
    for w in style_drift(ctx.get("stats", {}), body):
        add("warn", "style", f"문체 차이: {w}")
    heads = {b.text for b in post.blocks if b.kind == "heading"}
    for prev in ctx.get("recent_posts", []):
        ph = set(prev.get("headings", []))
        if heads and ph and len(heads & ph) / len(heads | ph) >= 0.5:
            add("warn", "structure_repeat", f"최근 글 '{prev['title']}' 과 소제목 구성이 비슷합니다")
            break
    if not ctx.get("notes"):
        add("warn", "no_notes", "이번 글만의 새 정보(--notes)가 없습니다 — 경험 정보가 없으면 노출에 불리")
    return L


REVISABLE = {
    "copied_sentence", "ai_tell", "style", "kw_title", "kw_intro", "kw_stuffing", "title_length", "title_symbol",
    "clickbait", "long_paragraph", "empty_caption", "urls", "engagement_bait", "brand_repeat", "title_repeat",
}

EDITOR_SYSTEM = """당신은 이 블로그 주인의 글을 오래 봐 온 편집자입니다. 아래 문체 프로필의 말투를 지키면서,
검사에서 지적된 부분만 최소한으로 고칩니다.

<style_profile>
{profile}
</style_profile>"""

EDITOR_USER = """초안의 블록 목록과 검사 결과입니다.

<draft>
제목: {title}
{blocks}
</draft>

<issues>
{issues}
</issues>

규칙:
- 지적된 곳만 고칩니다. 지적되지 않은 블록은 edits 에 넣지 않습니다.
- 고치면서 더 매끈하게 다듬지 않습니다. 말투·길이(±10%)·사진 배치는 그대로 둡니다.
- 사실·숫자·경험은 새로 만들지 않습니다. [확인필요: ...] 표시는 그대로 둡니다.
- 과거 글과 겹친다고 지적된 문장은 뜻은 유지하되 단어와 문장 구조를 새로 씁니다.
- 주 키워드: {keyword}"""


def revise_post(post: Post, lints: list[dict], profile: StyleProfile, keyword: str, cfg: dict) -> tuple[Post, int]:
    issues = [l for l in lints if l["code"] in REVISABLE]
    if not issues:
        return post, 0
    blocks = "\n".join(
        f"[{i}] ({b.kind}{' ' + b.photo if b.photo else ''}) {b.text}" for i, b in enumerate(post.blocks)
    )
    wcfg = cfg.get("writing", {})
    rev = llm.parse(
        model=wcfg.get("model", "claude-opus-5-5"),
        system=EDITOR_SYSTEM.format(profile=profile.model_dump_json(indent=2)),
        content=EDITOR_USER.format(
            title=post.title,
            blocks=blocks,
            issues="\n".join(f"- {l['msg']}" for l in issues),
            keyword=keyword,
        ),
        output=Revision,
        effort=wcfg.get("revise_effort", "medium"),
    )
    applied = 0
    if rev.title.strip():
        post.title = MARKDOWN_RE.sub("", rev.title).strip()
        applied += 1
    for e in rev.edits:
        new_text = MARKDOWN_RE.sub("", e.new_text).strip()
        if not (0 <= e.block_index < len(post.blocks)):
            continue
        block = post.blocks[e.block_index]
        if block.kind == "divider" or (not new_text and block.kind != "image"):
            continue
        block.text = new_text
        applied += 1
    return post, applied


# ---- 저장 / 미리보기 ------------------------------------------------------


def slugify(text: str) -> str:
    s = re.sub(r"[^\w가-힣]+", "-", text).strip("-")
    return s[:40] or "post"


def new_draft_dir(post: Post, drafts_root: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    d = drafts_root / f"{stamp}-{slugify(post.title)}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_draft(d: Path, post: Post, meta: dict) -> None:
    (d / "post.json").write_text(
        json.dumps({"post": post.model_dump(), "meta": meta}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (d / "preview.md").write_text(render_markdown(post, meta), encoding="utf-8")


def render_markdown(post: Post, meta: dict) -> str:
    images = meta.get("images", {})
    lines = [f"# {post.title}", ""]
    for b in post.blocks:
        if b.kind == "heading":
            lines += [f"## {b.text}", ""]
        elif b.kind == "text":
            lines += [b.text.replace("\n", "  \n"), ""]
        elif b.kind == "quote":
            lines += ["> " + b.text.replace("\n", "\n> "), ""]
        elif b.kind == "divider":
            lines += ["---", ""]
        else:
            lines += [f"![{b.text}](images/{images.get(b.photo, {}).get('file', '')})", ""]
            if b.text:
                lines += [f"*{b.text}*", ""]
    lines += ["태그: " + " ".join(f"#{t}" for t in post.tags), "", "---", "", "## 발행 전 확인", ""]
    lints = meta.get("lints", [])
    blocks = [l for l in lints if l["level"] == "block"]
    warns = [l for l in lints if l["level"] == "warn"]
    lines.append(f"- 공백 제외 {meta.get('chars_no_space', 0)}자, 사진 {len(images)}장")
    ov = meta.get("overlap", {})
    lines.append(f"- 가장 비슷한 과거 글: {ov.get('most_similar') or '-'} ({ov.get('max_ratio', 0):.0%})")
    if meta.get("revision"):
        r = meta["revision"]
        lines.append(f"- 편집 패스: {r['applied']}곳 수정 (경고 {r['before']}개 → {r['after']}개)")
    if blocks:
        lines += ["", "### 고치기 전에는 발행할 수 없음", *[f"- {l['msg']}" for l in blocks]]
    if warns:
        lines += ["", "### 확인 권장", *[f"- {l['msg']}" for l in warns]]
    lines += [
        "",
        "### 직접 확인",
        "- [ ] 내가 실제로 겪은 일이 사실대로 들어갔다",
        "- [ ] 가격·시간·수치가 지금 기준으로 맞다",
        "- [ ] 사진이 그 문단 내용과 맞다",
        "- [ ] 소리 내어 읽었을 때 내 말투다",
    ]
    return "\n".join(lines) + "\n"


def load_draft(draft_dir: Path) -> tuple[Post, dict]:
    data = json.loads((draft_dir / "post.json").read_text(encoding="utf-8"))
    return Post.model_validate(data["post"]), data["meta"]


def update_meta(draft_dir: Path, **changes) -> None:
    path = draft_dir / "post.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["meta"].update(changes)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
