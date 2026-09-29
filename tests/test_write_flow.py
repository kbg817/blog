import json
from types import SimpleNamespace

from PIL import Image

import blogbot.config as config
from blogbot import cli, llm
from blogbot.style import StyleProfile
from blogbot.writer import Block, Post


def test_write_creates_draft(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ROOT", tmp_path)
    (tmp_path / "data/manuscripts").mkdir(parents=True)
    (tmp_path / "data/photos").mkdir(parents=True)
    (tmp_path / "state").mkdir()
    (tmp_path / "data/manuscripts/old.txt").write_text("안녕하세요 이웃님들~ 오늘은 원두 이야기예요.", encoding="utf-8")
    Image.new("RGB", (1200, 800), "white").save(tmp_path / "data/photos/cup.jpg")
    (tmp_path / "state/photo_index.json").write_text(
        json.dumps({"cup.jpg": {"hash": "x", "description": "커피잔", "keywords": ["커피"]}}), encoding="utf-8"
    )
    profile = StyleProfile(
        tone="친근", sentence_endings=["~요"], self_reference="저", signature_phrases=[], emoji_and_symbols="",
        paragraph_style="짧게", structure=["도입", "본론", "마무리"], opening_patterns=[], closing_patterns=[],
        product_mention_style="자연스럽게", photo_usage="문단 사이", title_patterns=[], dos=[], donts=[],
    )
    (tmp_path / "state/style_profile.json").write_text(profile.model_dump_json(), encoding="utf-8")

    fake = Post(
        title="가을 원두 추천",
        blocks=[
            Block(kind="text", text="가을엔 역시 따뜻한 커피죠.", photo=""),
            Block(kind="image", text="오늘의 한 잔", photo="cup.jpg"),
            Block(kind="image", text="", photo="없는사진.jpg"),
            Block(kind="text", text="가격은 [확인필요: 가격] 입니다.", photo=""),
        ],
        tags=["원두", "커피"],
    )
    monkeypatch.setattr(llm, "parse", lambda **kw: fake)

    d = cli.cmd_write(SimpleNamespace(topic="가을 원두", keywords="원두", notes=""), {"writing": {}, "images": {}})
    data = json.loads((d / "post.json").read_text(encoding="utf-8"))
    assert [b["photo"] for b in data["post"]["blocks"] if b["kind"] == "image"] == ["cup.jpg"]
    assert data["meta"]["images"] == {"cup.jpg": "01.jpg"}
    assert data["meta"]["needs_check"] == ["[확인필요: 가격]"]
    assert (d / "images/01.jpg").exists()
    assert "images/01.jpg" in (d / "preview.md").read_text(encoding="utf-8")
