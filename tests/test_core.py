from datetime import datetime, timedelta

from PIL import Image

from blogbot.corpus import (
    Manuscript, overlap_report, pick_examples, split_sentences, style_drift, style_stats,
)
from blogbot.images import process_image
from blogbot.photos import Ledger


def _photo(path, size=(3000, 2000), color=(120, 160, 200)):
    im = Image.new("RGB", size, color)
    exif = Image.Exif()
    exif[0x010F] = "TestCamera"  # Make
    exif[0x8825] = {1: "N"}      # GPS
    im.save(path, "JPEG", exif=exif)
    return path


def test_process_image_downscales_strips_metadata_and_is_deterministic(tmp_path):
    src = _photo(tmp_path / "a.jpg")
    a, _ = process_image(src, tmp_path / "out" / "01.jpg", {"max_long_side": 2000})
    b, _ = process_image(src, tmp_path / "out" / "02.jpg", {"max_long_side": 2000})
    with Image.open(a) as im:
        assert im.size == (2000, 1333)
        assert len(im.getexif()) == 0          # 기기·GPS 정보 없음
        assert im.info.get("icc_profile")      # sRGB 프로필 명시
    assert a.read_bytes() == b.read_bytes()    # 무작위 변형 없음


def test_process_image_does_not_upscale_small_photos(tmp_path):
    src = _photo(tmp_path / "small.jpg", size=(600, 400))
    out, warns = process_image(src, tmp_path / "o.jpg", {"max_long_side": 2000, "min_width": 800})
    with Image.open(out) as im:
        assert im.size == (600, 400)
    assert warns and "작습니다" in warns[0]


def test_korean_watermark_skipped_without_font(tmp_path, monkeypatch):
    import blogbot.images as images

    monkeypatch.setattr(images, "_KOREAN_FONTS", ())
    src = _photo(tmp_path / "a.jpg", size=(800, 600))
    _, warns = process_image(src, tmp_path / "o.jpg", {"watermark_text": "@내블로그"})
    assert any("워터마크를 생략" in w for w in warns)


def test_overlap_is_per_document_and_ignores_whitelist():
    docs = [
        Manuscript("a.txt", "오늘은 우리 매장에 새로 들어온 원두를 소개해 드리려고 해요. 향이 정말 좋아요."),
        Manuscript("b.txt", "주말에 다녀온 캠핑장 후기를 남겨볼게요. 날씨가 맑았어요."),
    ]
    r = overlap_report("오늘은 우리 매장에 새로 들어온 원두를 소개해 드리려고 해요.", docs)
    assert r["max_ratio"] > 0.9 and r["most_similar"] == "a.txt" and r["copied_sentences"]

    cta = "궁금한 점은 댓글이나 네이버 톡톡으로 편하게 물어봐 주세요."
    docs2 = [Manuscript("c.txt", "지난주 이야기. " + cta)]
    new = "이번 주에는 밤라떼를 새로 내놨어요. 고소하고 은근히 달아요. " + cta
    assert overlap_report(new, docs2)["max_ratio"] > 0.3
    assert overlap_report(new, docs2, whitelist=[cta])["max_ratio"] < 0.05


def test_pick_examples_mixes_similar_and_different_topics():
    docs = [
        Manuscript("coffee", "커피 원두 로스팅 원두 이야기", datetime(2024, 1, 1)),
        Manuscript("dog", "강아지 산책 코스", datetime(2025, 1, 1)),
        Manuscript("camp", "캠핑장 후기", datetime(2025, 6, 1)),
    ]
    picked = pick_examples("원두 로스팅", docs, 3)
    assert picked[0][0].name == "coffee"
    assert {d.name for d, _ in picked[1:]} == {"dog", "camp"}


def test_split_sentences_handles_korean_without_periods():
    s = split_sentences("오늘 날씨가 정말 좋았어요 그래서 산책을 다녀왔답니다 ㅎㅎ 다음엔 친구랑 가보려구요")
    assert len(s) >= 2


def test_style_drift_flags_emoji_heavy_text():
    stats = style_stats(["담백하게 씁니다. 이모지는 안 써요. 문장은 짧아요."] * 3)
    warns = style_drift(stats, "오늘 너무 좋았어요 😀😀😀 정말 😍😍 최고예요 🎉🎉")
    assert any("이모지" in w for w in warns)


def test_ledger_photo_rules(tmp_path):
    led = Ledger(tmp_path / "ledger.json")
    new, archive = {"source": "new"}, {"source": "archive"}
    assert led.photo_available("n1", new, 90, 2)
    assert led.use_count("a1", archive) == 1                 # 예전 사진은 이미 1번 쓴 것으로
    led.record_photos(["n1"], "d")
    assert not led.photo_available("n1", new, 90, 2)          # 쿨다운
    led.data["photo_uses"]["n1"] = [{"at": (datetime.now() - timedelta(days=200)).isoformat(), "draft": "d"}]
    assert led.photo_available("n1", new, 90, 2)
    assert not led.photo_available("a1", archive, 90, 1)      # 예전 사진 + 최대 1회


def test_ledger_schedule_is_rolling(tmp_path):
    led = Ledger(tmp_path / "ledger.json")
    sched = {"max_posts_per_day": 1, "max_posts_per_week": 2, "min_interval_hours": 20}
    now = datetime(2026, 9, 29, 0, 10)
    assert led.schedule_block_reason(sched, now) == ""
    led.record_post(at=datetime(2026, 9, 28, 23, 50).isoformat(), status="published", title="t")
    assert "20시간" in led.schedule_block_reason(sched, now)   # 자정 넘겨도 막힘
    led.data["posts"][0]["at"] = (now - timedelta(hours=30)).isoformat()
    led.record_post(at=(now - timedelta(days=3)).isoformat(), status="published", title="t2")
    assert "7일" in led.schedule_block_reason(sched, now)
    led.data["posts"] = []
    led.data["halted"] = "누락"
    assert "중단" in led.schedule_block_reason(sched, now)
