"""Claude API 호출 래퍼."""

from __future__ import annotations

import base64
import io
from typing import TypeVar

import anthropic
from PIL import Image, ImageOps
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

_client: anthropic.Anthropic | None = None


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def parse(
    *,
    model: str,
    system: str,
    content: str | list,
    output: type[T],
    effort: str = "high",
    max_tokens: int = 64000,
) -> T:
    """구조화된 출력(Pydantic)으로 한 번 호출한다.

    - 스트리밍으로 받아 긴 응답에서도 HTTP 타임아웃이 나지 않게 한다.
    - max_tokens 는 사고(thinking) 토큰까지 포함한 상한이라 넉넉히 둔다.
    - 시스템 프롬프트(문체 프로필 등)는 매번 같으므로 캐시한다.
    - 정책상 거절(refusal)되면 서버 측 fallback 이 다른 모델로 재시도한다.
    """
    with client().beta.messages.stream(
        model=model,
        max_tokens=max_tokens,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": content}],
        output_config={"effort": effort},
        output_format=output,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    ) as stream:
        response = stream.get_final_message()

    if response.stop_reason == "refusal":
        raise RuntimeError("모델이 요청을 거절했습니다. 입력 내용을 확인해 주세요.")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("응답이 max_tokens 에서 잘렸습니다. effort 를 낮추거나 max_tokens 를 늘려주세요.")
    if response.parsed_output is None:
        raise RuntimeError("구조화된 응답을 해석하지 못했습니다.")
    return response.parsed_output


def image_block(path, max_side: int = 1024) -> dict:
    """비전 입력용으로 사진을 줄여 base64 이미지 블록을 만든다."""
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/jpeg",
            "data": base64.b64encode(buf.getvalue()).decode(),
        },
    }
