from datetime import datetime, timedelta

from PIL import Image

from blogbot.corpus import Manuscript, most_similar, overlap_report
from blogbot.images import process_image
from blogbot.photos import Ledger


def _photo(path, size=(1600, 1200)):
    im = Image.new("RGB", size, (120, 160, 200))
    exif = Image.Exif()
    exif[0x010F] = "TestCamera"  # Make
    im.save(path, "JPEG", exif=exif)
    return path


def test_process_image_resizes_and_strips_metadata(tmp_path):
    src = _photo(tmp_path / "a.jpg")
    dst = process_image(src, tmp_path / "out" / "01.jpg", {"width": 900, "watermark_text": "@me"}, seed=1)
    with Image.open(dst) as im:
        assert im.width == 900
        assert len(im.getexif()) == 0
    # 시드가 다르면 결과도 다르다
    other = process_image(src, tmp_path / "out" / "02.jpg", {"width": 900}, seed=2)
    assert dst.read_bytes() != other.read_bytes()


def test_overlap_detects_copied_sentences():
    docs = [Manuscript("a.txt", "오늘은 우리 매장에 새로 들어온 원두를 소개해 드리려고 해요. 향이 정말 좋아요.")]
    ratio, copied = overlap_report("오늘은 우리 매장에 새로 들어온 원두를 소개해 드리려고 해요.", docs)
    assert ratio > 0.9 and copied
    ratio, copied = overlap_report("주말에 다녀온 캠핑장 후기를 남겨볼게요. 날씨가 맑았어요.", docs)
    assert ratio < 0.1 and not copied


def test_most_similar():
    docs = [Manuscript("a", "커피 원두 로스팅 이야기"), Manuscript("b", "강아지 산책 코스 추천")]
    assert most_similar("원두 로스팅 방법", docs, 1)[0].name == "a"


def test_ledger_limits(tmp_path):
    led = Ledger(tmp_path / "ledger.json")
    assert led.photo_available("p.jpg", 30, 3)
    led.record_photos(["p.jpg"])
    assert not led.photo_available("p.jpg", 30, 3)  # 쿨다운
    led.data["photo_uses"]["p.jpg"] = [(datetime.now() - timedelta(days=40)).isoformat()] * 3
    assert not led.photo_available("p.jpg", 30, 3)  # 최대 사용 횟수
    led.record_post("d", "t", "review")
    assert led.posts_today() == 1
