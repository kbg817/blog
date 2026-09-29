"""과거 원고/사진 불러오기, 텍스트 유사도, 문체 통계."""

from __future__ import annotations

import math
import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

TEXT_EXTS = {".txt", ".md"}
DOCX_EXTS = {".docx"}
PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
PHOTO_MARK = "[사진]"


@dataclass
class Manuscript:
    name: str
    text: str
    date: datetime | None = field(default=None)
    title: str = ""


def read_text_file(path: Path) -> str:
    # utf-8-sig 를 먼저 시도해야 BOM 이 본문에 남지 않는다.
    for enc in ("utf-8-sig", "cp949"):
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="ignore")


def read_docx(path: Path) -> str:
    """문단 텍스트를 읽고, 사진이 있던 자리는 [사진] 표식으로 남긴다 (사진 배치 습관 학습용)."""
    import docx

    lines = []
    for p in docx.Document(str(path)).paragraphs:
        has_image = bool(p._element.xpath(".//w:drawing"))
        text = p.text.strip()
        if text:
            lines.append(text)
        if has_image:
            lines.append(PHOTO_MARK)
        if not text and not has_image:
            lines.append("")
    return "\n".join(lines)


_DATE_RE = re.compile(r"(20\d{2})[-_.]?(\d{2})[-_.]?(\d{2})")


def _guess_date(path: Path) -> datetime:
    m = _DATE_RE.search(path.stem)
    if m:
        try:
            return datetime(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            pass
    return datetime.fromtimestamp(path.stat().st_mtime)


def load_manuscripts(folder: Path) -> list[Manuscript]:
    """folder 아래(하위 폴더 포함)의 .txt/.md/.docx 원고를 모두 읽는다. 첫 줄은 제목으로 본다."""
    items: list[Manuscript] = []
    if not folder.exists():
        return items
    for path in sorted(folder.rglob("*")):
        ext = path.suffix.lower()
        if ext in TEXT_EXTS:
            text = read_text_file(path)
        elif ext in DOCX_EXTS:
            text = read_docx(path)
        else:
            continue
        text = text.strip()
        if text:
            first = text.splitlines()[0].strip().lstrip("#").strip()
            items.append(
                Manuscript(str(path.relative_to(folder)), text, _guess_date(path), first[:80])
            )
    return items


def list_photos(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    return sorted(
        p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in PHOTO_EXTS
    )


# ---- 유사도 -------------------------------------------------------------

def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text.replace(PHOTO_MARK, ""))


def char_ngrams(text: str, n: int = 3) -> Counter:
    s = _normalize(text)
    return Counter(s[i : i + n] for i in range(len(s) - n + 1))


def gram_set(text: str, n: int) -> set[str]:
    s = _normalize(text)
    return {s[i : i + n] for i in range(len(s) - n + 1)}


def cosine(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    dot = sum(v * b.get(k, 0) for k, v in a.items())
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb)


def similarity(a: str, b: str) -> float:
    return cosine(char_ngrams(a, 2), char_ngrams(b, 2))


def pick_examples(query: str, docs: list[Manuscript], k: int) -> list[tuple[Manuscript, str]]:
    """예시 원고 선택: 주제가 가장 비슷한 1편 + 주제가 다른 최근 글 (k-1)편.

    비슷한 글만 여러 편 넣으면 문장·구성·옛 사실이 새 글로 복사될 위험이 커진다.
    주제가 다른 글은 '말투'만 보여주므로 복사 위험 없이 문체를 전달한다.
    """
    if not docs or k <= 0:
        return []
    q = char_ngrams(query, 2)
    scored = sorted(docs, key=lambda d: cosine(q, char_ngrams(d.text, 2)), reverse=True)
    picked = [(scored[0], "주제가 비슷한 글: 말투와 설명 방식만 참고")]
    rest = sorted(scored[len(scored) // 2 :] or scored[1:], key=lambda d: d.date or datetime.min, reverse=True)
    for d in rest:
        if len(picked) >= k:
            break
        if d is not scored[0]:
            picked.append((d, "주제가 다른 최근 글: 말투·리듬만 참고"))
    return picked


def split_sentences(text: str, min_len: int = 10) -> list[str]:
    text = text.replace(PHOTO_MARK, "\n")
    # 마침표·물음표·느낌표·물결 뒤, 또는 마침표 없이 끝나는 흔한 종결어미(요/다/죠…)·ㅎㅎ 뒤 공백에서 나눈다.
    text = re.sub(r"([.!?。~…]|(?<=[가-힣])[요다죠네까음함]|[ㅎㅋ]{2,}|\^\^)[ \t]+", "\\1\n", text)
    return [p.strip() for p in text.split("\n") if len(_normalize(p)) >= min_len]


def overlap_report(
    new_text: str, docs: list[Manuscript], whitelist: list[str] | None = None, n: int = 8
) -> dict:
    """새 글이 과거 글과 얼마나 겹치는지 (문서별로 계산해 가장 높은 값).

    네이버 유사문서 판정은 문서 대 문서 비교이므로 '과거 글 전체의 합집합'이 아니라
    가장 비슷한 한 편과의 겹침이 기준이다. whitelist(마무리 안내문, 확정 정보, 늘 쓰는
    인사말)는 매번 같아도 되는 문구라 겹침에서 뺀다.
    """
    wl: set[str] = set()
    for w in whitelist or []:
        wl |= gram_set(w, n)

    s = _normalize(new_text)
    grams = [g for g in (s[i : i + n] for i in range(len(s) - n + 1)) if g not in wl]
    result = {"max_ratio": 0.0, "most_similar": "", "copied_sentences": []}
    if not grams:
        return result

    all_grams: set[str] = set()
    for d in docs:
        dg = gram_set(d.text, n) - wl
        all_grams |= dg
        ratio = sum(g in dg for g in grams) / len(grams)
        if ratio > result["max_ratio"]:
            result["max_ratio"], result["most_similar"] = ratio, d.name

    for sent in split_sentences(new_text):
        ss = _normalize(sent)
        sg = [g for g in (ss[i : i + n] for i in range(len(ss) - n + 1)) if g not in wl]
        if len(sg) >= 4 and sum(g in all_grams for g in sg) / len(sg) > 0.6:
            result["copied_sentences"].append(sent)
    return result


# ---- 문체 통계 (LLM 이 아니라 코드로 계산) ---------------------------------

_ENDING_RE = re.compile(r"([가-힣]{1,3})[.!?~…ㅎㅋ^\s]*$")
_EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF☀-➿]")


def style_stats(texts: list[str]) -> dict:
    """문장 길이 분포, 끝맺음, ㅎㅎ/이모지/물결 밀도 등 정량 문체 특징."""
    sentences = [s for t in texts for s in split_sentences(t, min_len=3)]
    lengths = [len(_normalize(s)) for s in sentences] or [0]
    endings = Counter()
    for s in sentences:
        m = _ENDING_RE.search(s)
        if m:
            endings[m.group(1)[-2:]] += 1
    joined = "\n".join(t.replace(PHOTO_MARK, "") for t in texts)
    chars = max(1, len(_normalize(joined)))
    per_k = lambda n: round(n * 1000 / chars, 2)  # noqa: E731
    paragraphs = [p for t in texts for p in re.split(r"\n\s*\n", t) if p.strip()]
    para_sent = [len(split_sentences(p, min_len=3)) for p in paragraphs] or [0]
    q = statistics.quantiles(lengths, n=10) if len(lengths) >= 10 else [min(lengths)] * 9
    total_endings = sum(endings.values()) or 1
    return {
        "docs": len(texts),
        "chars_per_doc_median": int(statistics.median([len(_normalize(t)) for t in texts] or [0])),
        "sentence_len_p10": int(q[0]),
        "sentence_len_p50": int(statistics.median(lengths)),
        "sentence_len_p90": int(q[-1]),
        "sentences_per_paragraph_median": statistics.median(para_sent),
        "top_endings": [
            {"ending": e, "ratio": round(c / total_endings, 3)} for e, c in endings.most_common(10)
        ],
        "laugh_per_1k": per_k(len(re.findall(r"[ㅎㅋ]{2,}", joined))),
        "emoji_per_1k": per_k(len(_EMOJI_RE.findall(joined))),
        "tilde_per_1k": per_k(joined.count("~")),
        "exclaim_per_1k": per_k(joined.count("!")),
        "question_per_1k": per_k(joined.count("?")),
        "photos_per_doc_median": statistics.median([t.count(PHOTO_MARK) for t in texts] or [0]),
    }


def style_drift(stats: dict, text: str) -> list[str]:
    """생성 글이 원고 통계에서 크게 벗어나면 경고 문구 목록."""
    if not stats or not stats.get("docs"):
        return []
    new = style_stats([text])
    warns = []
    base, cur = stats["sentence_len_p50"], new["sentence_len_p50"]
    if base and not (0.7 * base <= cur <= 1.3 * base):
        warns.append(f"문장 길이 중앙값 {cur}자 (원고 {base}자)")
    for key, label in (("laugh_per_1k", "ㅎㅎ/ㅋㅋ"), ("emoji_per_1k", "이모지"), ("tilde_per_1k", "물결(~)")):
        b, c = stats.get(key, 0), new.get(key, 0)
        if b >= 0.5 and (c < b * 0.4 or c > b * 2.5):
            warns.append(f"{label} 빈도 {c}/1000자 (원고 {b})")
        elif b < 0.2 and c >= 1.0:
            warns.append(f"{label} 빈도 {c}/1000자 (원고에서는 거의 안 씀)")
    return warns
