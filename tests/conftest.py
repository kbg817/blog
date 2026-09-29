import json

import pytest
from PIL import Image

import blogbot.config as config
from blogbot.style import StyleProfile


def make_profile(**over) -> StyleProfile:
    base = dict(
        tone="친근", sentence_endings=["~요"], self_reference="저", reader_address="여러분", vocabulary="쉬움",
        sentence_rhythm="짧게", humor_and_emotion="ㅎㅎ", information_density="보통", experience_narration="시간순",
        numbers_and_names="가격 명시", punctuation="~", emoji_and_symbols="적음", paragraph_style="짧게",
        layout="소제목 없음", structure=["도입", "본론", "마무리"], opening_patterns=[], closing_patterns=[],
        signature_phrases=["안녕하세요 이웃님들~"], product_mention_style="자연스럽게", photo_usage="원고에서 확인 불가",
        caption_style="원고에서 확인 불가", title_patterns=[], topics=["카페"], recent_shift="없음",
        dos=[], donts=[], evidence=[],
    )
    base.update(over)
    return StyleProfile(**base)


@pytest.fixture
def project(tmp_path, monkeypatch):
    """임시 프로젝트 폴더: config, 원고 1편, 예전 사진 2장, 새 사진 폴더, 문체 프로필."""
    monkeypatch.setattr(config, "ROOT", tmp_path)
    (tmp_path / "data/manuscripts").mkdir(parents=True)
    (tmp_path / "data/photos").mkdir(parents=True)
    (tmp_path / "state").mkdir()
    (tmp_path / "data/manuscripts/20240101-원두.txt").write_text(
        "새 원두 들어왔어요\n안녕하세요 이웃님들~ 오늘은 새로 들어온 에티오피아 원두 이야기를 해볼게요.\n산미가 살짝 있고 꽃향이 나요.",
        encoding="utf-8",
    )
    for i, color in enumerate(["white", "gray"]):
        Image.new("RGB", (1200, 800), color).save(tmp_path / f"data/photos/old{i}.jpg")
    (tmp_path / "state/style_profile.json").write_text(make_profile().model_dump_json(), encoding="utf-8")
    (tmp_path / "state/style_stats.json").write_text(json.dumps({}), encoding="utf-8")
    return tmp_path


@pytest.fixture
def cfg():
    return {
        "naver": {"blog_id": "tester", "mode": "review"},
        "business": {"name": "테스트카페", "relationship": "owner", "disclosure": "제가 운영하는 카페 이야기예요.",
                     "facts": ["영업시간: 10:00~19:00"], "call_to_action": "궁금한 점은 톡톡으로 물어봐 주세요."},
        "writing": {"review_passes": 0, "length_no_space": {"min": 10}},
        "images": {},
        "schedule": {},
    }
