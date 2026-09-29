"""업로드용 사진 편집.

원본 사진에 일반적인 보정(크롭·리사이즈·밝기/대비/채도·워터마크)을 적용하고
촬영 기기·GPS 같은 메타데이터를 제거한 새 JPEG 를 만든다.
글마다 보정값이 조금씩 달라지도록 시드를 글 단위로 준다.

참고: 이런 보정은 사진을 글에 맞게 다듬는 용도이지, 같은 사진의 반복 사용을
감추는 수단으로 믿으면 안 된다. 재사용 자체를 줄이는 건 photos.Ledger 가 담당한다.
"""

from __future__ import annotations

import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps


def _uniform(rng: random.Random, pair, default):
    lo, hi = pair if pair else default
    return rng.uniform(lo, hi)


def _font(size: int) -> ImageFont.ImageFont:
    for name in (
        "NanumGothicBold.ttf",
        "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",
        "C:/Windows/Fonts/malgunbd.ttf",
        "DejaVuSans-Bold.ttf",
    ):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def process_image(src: Path, dst: Path, cfg: dict, seed: int) -> Path:
    rng = random.Random(seed)
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im)  # 회전 정보 반영 후 메타데이터는 버린다
        im = im.convert("RGB")

    # 1) 가장자리 크롭 (구도 정리, 비율은 원본 유지)
    frac = _uniform(rng, cfg.get("crop_range"), (0.02, 0.06))
    w, h = im.size
    dx, dy = int(w * frac), int(h * frac)
    ox = rng.randint(0, dx)
    oy = rng.randint(0, dy)
    im = im.crop((ox, oy, w - (dx - ox), h - (dy - oy)))

    # 2) 본문 폭에 맞게 리사이즈
    target_w = int(cfg.get("width", 900))
    if im.width != target_w:
        im = im.resize((target_w, round(im.height * target_w / im.width)), Image.LANCZOS)

    # 3) 톤 보정
    im = ImageEnhance.Brightness(im).enhance(_uniform(rng, cfg.get("brightness_range"), (0.96, 1.05)))
    im = ImageEnhance.Contrast(im).enhance(_uniform(rng, cfg.get("contrast_range"), (0.97, 1.05)))
    im = ImageEnhance.Color(im).enhance(_uniform(rng, cfg.get("saturation_range"), (0.96, 1.06)))
    im = im.filter(ImageFilter.UnsharpMask(radius=1.2, percent=60, threshold=3))

    # 4) 워터마크 / 로고
    im = _apply_branding(im, cfg, rng)

    dst.parent.mkdir(parents=True, exist_ok=True)
    # exif 를 넘기지 않으므로 메타데이터 없이 저장된다.
    im.save(dst, "JPEG", quality=int(cfg.get("jpeg_quality", 90)), optimize=True, progressive=True)
    return dst


def _apply_branding(im: Image.Image, cfg: dict, rng: random.Random) -> Image.Image:
    text = (cfg.get("watermark_text") or "").strip()
    logo_path = (cfg.get("watermark_logo") or "").strip()
    if not text and not logo_path:
        return im

    im = im.convert("RGBA")
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    margin = int(im.width * 0.03)
    corner = rng.choice(["br", "bl"])

    if logo_path and Path(logo_path).exists():
        with Image.open(logo_path) as logo:
            logo = logo.convert("RGBA")
            lw = int(im.width * 0.15)
            logo = logo.resize((lw, round(logo.height * lw / logo.width)), Image.LANCZOS)
            alpha = logo.getchannel("A").point(lambda a: int(a * 0.8))
            logo.putalpha(alpha)
            x = im.width - lw - margin if corner == "br" else margin
            y = im.height - logo.height - margin
            layer.alpha_composite(logo, (x, y))
    elif text:
        draw = ImageDraw.Draw(layer)
        font = _font(max(14, im.width // 40))
        l, t, r, b = draw.textbbox((0, 0), text, font=font)
        tw, th = r - l, b - t
        x = im.width - tw - margin if corner == "br" else margin
        y = im.height - th - margin
        draw.text((x + 1, y + 1), text, font=font, fill=(0, 0, 0, 90))
        draw.text((x, y), text, font=font, fill=(255, 255, 255, 190))

    return Image.alpha_composite(im, layer).convert("RGB")
