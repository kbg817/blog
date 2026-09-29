"""업로드용 사진 편집.

하는 일 (모든 글에 같은 방식으로, 무작위 변형 없음):
- 촬영 방향 반영 (세로 사진이 눕지 않게)
- 색 공간을 sRGB 로 변환 (아이폰 P3 사진이 업로드 후 바래 보이는 문제 방지)
- 긴 변을 max_long_side 로 축소 (작은 사진은 확대하지 않음)
- 축소한 경우에만 약한 샤픈
- 선택: 작은 워터마크(블로그 ID 등)
- 촬영 기기·GPS 등 메타데이터 제거 (개인정보 보호 목적)

하지 않는 일:
- 같은 사진을 다른 사진처럼 보이게 하는 변형. 네이버는 유사문서 판정에서 사진도 비교하며,
  크롭·색보정·해시 변경으로 이를 피할 수 있다는 근거는 없다. 재사용 자체를 줄이는 것은
  photos.Ledger 가 담당한다.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

from PIL import Image, ImageCms, ImageDraw, ImageFilter, ImageFont, ImageOps

_KOREAN_FONTS = (
    "NanumGothicBold.ttf",
    "NanumGothic.ttf",
    "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf",
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
    "C:/Windows/Fonts/malgunbd.ttf",
    "C:/Windows/Fonts/malgun.ttf",
)
_LATIN_FONTS = ("DejaVuSans-Bold.ttf", "Arial.ttf", "C:/Windows/Fonts/arialbd.ttf")
_SRGB = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))


class ImageWarning(Exception):
    pass


def _find_font(size: int, text: str, configured: str = "") -> ImageFont.FreeTypeFont | None:
    needs_korean = bool(re.search(r"[가-힣]", text))
    candidates = ([configured] if configured else []) + list(_KOREAN_FONTS)
    if not needs_korean:
        candidates += list(_LATIN_FONTS)
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return None


def _to_srgb(im: Image.Image) -> Image.Image:
    icc = im.info.get("icc_profile")
    if icc:
        try:
            src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            return ImageCms.profileToProfile(im, src, _SRGB, outputMode="RGB")
        except Exception:
            pass
    return im.convert("RGB")


def process_image(src: Path, dst: Path, cfg: dict) -> tuple[Path, list[str]]:
    """사진을 업로드용으로 저장하고 (경로, 경고 목록)을 돌려준다."""
    warnings: list[str] = []
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im)
        im = _to_srgb(im)

    max_side = int(cfg.get("max_long_side", 2000))
    min_width = int(cfg.get("min_width", 800))
    if max(im.size) > max_side:
        scale = max_side / max(im.size)
        im = im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)
        im = im.filter(ImageFilter.UnsharpMask(radius=1.0, percent=35, threshold=3))
    if im.width < min_width:
        warnings.append(f"{src.name}: 가로 {im.width}px 로 작습니다 (확대하지 않음).")

    im, wm_warn = _apply_branding(im, cfg)
    if wm_warn:
        warnings.append(wm_warn)

    dst.parent.mkdir(parents=True, exist_ok=True)
    # exif 를 넘기지 않으므로 촬영 기기·GPS 정보 없이 저장된다. 색 프로필은 sRGB 로 명시.
    im.save(
        dst,
        "JPEG",
        quality=int(cfg.get("jpeg_quality", 92)),
        optimize=True,
        progressive=True,
        icc_profile=_SRGB.tobytes(),
    )
    return dst, warnings


def _apply_branding(im: Image.Image, cfg: dict) -> tuple[Image.Image, str]:
    text = (cfg.get("watermark_text") or "").strip()
    logo_path = (cfg.get("watermark_logo") or "").strip()
    if not text and not logo_path:
        return im, ""

    im = im.convert("RGBA")
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    margin = int(im.width * 0.025)
    warn = ""

    if logo_path and Path(logo_path).exists():
        with Image.open(logo_path) as logo:
            logo = logo.convert("RGBA")
            lw = int(im.width * min(float(cfg.get("watermark_scale", 0.08)), 0.1))
            logo = logo.resize((lw, max(1, round(logo.height * lw / logo.width))), Image.LANCZOS)
            logo.putalpha(logo.getchannel("A").point(lambda a: int(a * 0.7)))
            layer.alpha_composite(logo, (im.width - lw - margin, im.height - logo.height - margin))
    elif text:
        font = _find_font(max(14, im.width // 45), text, cfg.get("watermark_font", ""))
        if font is None:
            warn = "한글 폰트를 찾지 못해 워터마크를 생략했습니다 (images.watermark_font 에 폰트 경로 지정)."
        else:
            draw = ImageDraw.Draw(layer)
            left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
            x = im.width - (right - left) - margin
            y = im.height - (bottom - top) - margin
            draw.text((x + 1, y + 1), text, font=font, fill=(0, 0, 0, 80))
            draw.text((x, y), text, font=font, fill=(255, 255, 255, 170))

    return Image.alpha_composite(im, layer).convert("RGB"), warn
