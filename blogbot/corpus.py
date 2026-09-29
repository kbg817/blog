"""과거 원고/사진 불러오기와 텍스트 유사도 계산."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

TEXT_EXTS = {".txt", ".md"}
DOCX_EXTS = {".docx"}
PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


@dataclass
class Manuscript:
    name: str
    text: str


def read_text_file(path: Path) -> str:
    for enc in ("utf-8", "utf-8-sig", "cp949"):
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="ignore")


def read_docx(path: Path) -> str:
    import docx

    return "\n".join(p.text for p in docx.Document(str(path)).paragraphs)


def load_manuscripts(folder: Path) -> list[Manuscript]:
    """folder 아래(하위 폴더 포함)의 .txt/.md/.docx 원고를 모두 읽는다."""
    items: list[Manuscript] = []
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
            items.append(Manuscript(str(path.relative_to(folder)), text))
    return items


def list_photos(folder: Path) -> list[Path]:
    return sorted(
        p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in PHOTO_EXTS
    )


# ---- 유사도 -------------------------------------------------------------

def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text)


def char_ngrams(text: str, n: int = 3) -> Counter:
    s = _normalize(text)
    return Counter(s[i : i + n] for i in range(len(s) - n + 1))


def cosine(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    dot = sum(v * b.get(k, 0) for k, v in a.items())
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb)


def most_similar(query: str, docs: list[Manuscript], k: int) -> list[Manuscript]:
    """주제와 가장 비슷한 과거 원고 k개 (예시로 넣을 원고 선택용)."""
    q = char_ngrams(query, 2)
    scored = sorted(docs, key=lambda d: cosine(q, char_ngrams(d.text, 2)), reverse=True)
    return scored[:k]


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?。~])\s+|\n+", text)
    return [p.strip() for p in parts if len(_normalize(p)) >= 12]


def overlap_report(new_text: str, docs: list[Manuscript], n: int = 8) -> tuple[float, list[str]]:
    """새 글이 과거 원고와 얼마나 겹치는지.

    반환: (과거 원고와 겹치는 8글자 조각 비율, 거의 그대로 복사된 문장 목록)
    네이버는 기존 문서와 거의 같은 글을 유사문서로 보고 노출을 낮추므로,
    문체는 닮되 문장은 새로 써야 한다.
    """
    corpus_grams: set[str] = set()
    for d in docs:
        s = _normalize(d.text)
        corpus_grams.update(s[i : i + n] for i in range(len(s) - n + 1))

    s = _normalize(new_text)
    grams = [s[i : i + n] for i in range(len(s) - n + 1)]
    ratio = sum(g in corpus_grams for g in grams) / len(grams) if grams else 0.0

    copied = []
    for sent in split_sentences(new_text):
        ss = _normalize(sent)
        sg = [ss[i : i + n] for i in range(len(ss) - n + 1)]
        if sg and sum(g in corpus_grams for g in sg) / len(sg) > 0.6:
            copied.append(sent)
    return ratio, copied
