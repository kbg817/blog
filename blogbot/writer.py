"""문체 프로필 + 비슷한 과거 원고 + 사업 정보로 새 글을 쓴다."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from blogbot import llm
from blogbot.corpus import Manuscript, most_similar, overlap_report
from blogbot.style import StyleProfile


class Block(BaseModel):
    kind: Literal["heading", "text", "image"]
    text: str = Field(description="heading/text: 내용. image: 사진 아래 짧은 설명(없으면 빈 문자열)")
    photo: str = Field(description="image 일 때 사진 목록의 파일 경로 그대로. 그 외에는 빈 문자열")


class Post(BaseModel):
    title: str
    blocks: list[Block]
    tags: list[str] = Field(description="네이버 태그 5~10개, # 없이")


SYSTEM_TEMPLATE = """당신은 아래 문체 프로필의 주인공 본인이 되어 네이버 블로그 글을 씁니다.

<style_profile>
{profile}
</style_profile>

<business>
{business}
</business>

작성 원칙:
- 말투, 문장 끝맺음, 문단 길이, 글의 흐름은 문체 프로필과 예시 원고를 따른다.
- 예시 원고의 문장을 그대로 옮기지 않는다. 같은 사람이 새 경험을 새로 쓴 것처럼 모든 문장을 새로 쓴다.
  (과거 글과 문장이 겹치면 네이버에서 유사문서로 판단해 노출이 떨어진다.)
- <business> 에 없는 사실(가격, 수치, 효능, 후기, 수상 경력 등)을 지어내지 않는다.
  구체적인 정보가 필요하면 [확인필요: ...] 형태로 남겨 작성자가 채우게 한다.
- 검색하는 사람에게 실제로 도움이 되는 정보를 중심으로 쓰고, 제품 언급은 문체 프로필의 방식대로 자연스럽게 한다.
- 키워드를 억지로 반복하지 않는다.
- 사진은 제공된 사진 목록에서만 고르고, 사진 설명과 맞는 문단 옆에 배치한다. 같은 사진을 두 번 쓰지 않는다.
"""


def _business_text(biz: dict) -> str:
    facts = "\n".join(f"- {f}" for f in biz.get("facts", []) or [])
    return (
        f"이름: {biz.get('name', '')}\n"
        f"소개: {biz.get('description', '').strip()}\n"
        f"확정 정보:\n{facts}\n"
        f"마무리 안내 문구: {biz.get('call_to_action', '')}"
    )


def write_post(
    *,
    topic: str,
    keywords: list[str],
    notes: str,
    profile: StyleProfile,
    docs: list[Manuscript],
    photo_candidates: dict[str, dict],
    cfg: dict,
) -> Post:
    wcfg = cfg.get("writing", {})
    examples = most_similar(f"{topic} {' '.join(keywords)}", docs, int(wcfg.get("examples_per_prompt", 3)))
    example_text = "\n\n".join(
        f'<example name="{d.name}">\n{d.text}\n</example>' for d in examples
    )
    photo_list = "\n".join(
        f"- {key}: {v.get('description', '')} (키워드: {', '.join(v.get('keywords', []))})"
        for key, v in photo_candidates.items()
    ) or "(사용 가능한 사진 없음 — image 블록을 쓰지 마세요)"

    system = SYSTEM_TEMPLATE.format(
        profile=profile.model_dump_json(indent=2),
        business=_business_text(cfg.get("business", {})),
    )
    user = f"""<examples>
{example_text}
</examples>

<photos>
{photo_list}
</photos>

새 글을 써주세요.
- 주제: {topic}
- 검색 키워드: {', '.join(keywords) or '(자유)'}
- 추가 메모: {notes or '(없음)'}
- 본문 분량: 약 {wcfg.get('target_length', 1800)}자
- 사진: {wcfg.get('images_per_post', 5)}장 내외"""

    post = llm.parse(
        model=wcfg.get("model", "claude-opus-5-5"),
        system=system,
        content=user,
        output=Post,
        effort=wcfg.get("effort", "high"),
    )
    # 목록에 없는 사진이나 중복 사진은 제거
    seen: set[str] = set()
    cleaned = []
    for b in post.blocks:
        if b.kind == "image":
            if b.photo not in photo_candidates or b.photo in seen:
                continue
            seen.add(b.photo)
        cleaned.append(b)
    post.blocks = cleaned
    return post


def post_plain_text(post: Post) -> str:
    return "\n".join(b.text for b in post.blocks if b.kind != "image")


def slugify(text: str) -> str:
    s = re.sub(r"[^\w가-힣]+", "-", text).strip("-")
    return s[:40] or "post"


def new_draft_dir(post: Post, drafts_root: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    d = drafts_root / f"{stamp}-{slugify(post.title)}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_draft(
    d: Path, post: Post, docs: list[Manuscript], max_overlap: float, images: dict[str, str]
) -> Path:
    """images: 원본 사진 경로 → 초안 폴더 안의 가공된 파일 이름"""
    ratio, copied = overlap_report(post_plain_text(post), docs)
    meta = {
        "overlap_ratio": round(ratio, 3),
        "overlap_warning": ratio > max_overlap,
        "copied_sentences": copied,
        "needs_check": re.findall(r"\[확인필요:[^\]]*\]", post_plain_text(post)),
        "images": images,
        "status": "draft",
    }
    (d / "post.json").write_text(
        json.dumps({"post": post.model_dump(), "meta": meta}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (d / "preview.md").write_text(render_markdown(post, meta), encoding="utf-8")
    return d


def render_markdown(post: Post, meta: dict) -> str:
    lines = [f"# {post.title}", ""]
    for b in post.blocks:
        if b.kind == "heading":
            lines += [f"## {b.text}", ""]
        elif b.kind == "text":
            lines += [b.text, ""]
        else:
            lines += [f"![{b.text}](images/{meta['images'].get(b.photo, '')})", ""]
            if b.text:
                lines += [f"*{b.text}*", ""]
    lines += ["", "태그: " + " ".join(f"#{t}" for t in post.tags), "", "---", ""]
    lines.append(f"과거 원고와 겹치는 비율: {meta['overlap_ratio']:.1%}")
    if meta["copied_sentences"]:
        lines.append("거의 그대로 겹치는 문장 (수정 권장):")
        lines += [f"- {s}" for s in meta["copied_sentences"]]
    if meta["needs_check"]:
        lines.append("직접 채워야 할 부분:")
        lines += [f"- {s}" for s in meta["needs_check"]]
    return "\n".join(lines) + "\n"


def load_draft(draft_dir: Path) -> tuple[Post, dict]:
    data = json.loads((draft_dir / "post.json").read_text(encoding="utf-8"))
    return Post.model_validate(data["post"]), data["meta"]


def update_meta(draft_dir: Path, **changes) -> None:
    path = draft_dir / "post.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["meta"].update(changes)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
