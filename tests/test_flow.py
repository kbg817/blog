import json
from argparse import Namespace
from datetime import datetime, timedelta

import pytest
from PIL import Image

from blogbot import cli, llm, publisher
from blogbot.photos import Ledger
from blogbot.writer import Block, Claim, Post, load_draft


def fake_post(**over):
    base = dict(
        search_intent="가을 신메뉴가 궁금함",
        new_info=["밤라떼 출시"],
        title="밤라떼 출시, 가을 신메뉴 직접 만들어 본 후기",
        blocks=[
            Block(kind="text", text="이번 주에 밤라떼를 새로 내놨어요.\n2026년 9월 기준 5,500원이에요.", photo=""),
            Block(kind="image", text="갓 만든 밤라떼 한 잔", photo="P1"),
            Block(kind="image", text="예전 사진 1", photo="P2"),
            Block(kind="image", text="예전 사진 2", photo="P3"),
            Block(kind="heading", text="**맛은 어떤가요**", photo=""),
            Block(kind="text", text="밤라떼는 고소하고 은근히 달아요.\n우유는 따뜻하게 데워서 밤 페이스트와 섞고, 위에 에스프레소를 부어요.\n"
                  "처음 몇 잔은 단맛이 너무 강해서 페이스트 양을 줄였어요.\n지금은 한 모금 마시면 밤 향이 먼저 나고, 커피 맛이 뒤에 따라와요.\n"
                  "아이스로도 되는데 따뜻하게 드시는 걸 더 추천해요.\n날이 쌀쌀해지면 오후에 한 잔씩 찾으시는 분이 많더라고요.\n시럽 추가는 따로 말씀해 주시면 돼요.", photo=""),
        ],
        tags=["#밤라떼", "가을 신메뉴", "밤라떼", "카페"],
        claims=[Claim(text="5,500원", source="notes")],
    )
    base.update(over)
    return Post(**base)


def write_args(project, **over):
    new_dir = project / "new"
    new_dir.mkdir(exist_ok=True)
    Image.new("RGB", (1600, 1200), "orange").save(new_dir / "latte.jpg")
    base = dict(topic="가을 신메뉴", keyword="밤라떼", sub_keywords="가을 신메뉴", notes="밤라떼 출시, 5,500원, 고소하고 은근히 달다",
                notes_file=None, photos=str(new_dir), kind="experience", allow_empty_notes=False)
    base.update(over)
    return Namespace(**base)


@pytest.fixture
def mock_llm(monkeypatch):
    """사진 설명과 글 생성을 가짜로. write 에 넘어간 사진 ID 순서를 기록."""
    seen = {}

    def parse(**kw):
        name = kw["output"].__name__
        if name == "PhotoDescription":
            return kw["output"](description="사진", keywords=["카페"], specific_details=[], orientation="가로")
        if name == "Post":
            seen["user"] = kw["content"]
            return seen.get("post") or fake_post()
        raise AssertionError(name)

    monkeypatch.setattr(llm, "parse", parse)
    return seen


def test_write_requires_notes(project, cfg, mock_llm):
    with pytest.raises(SystemExit, match="--notes"):
        cli.cmd_write(write_args(project, notes=""), cfg)


def test_write_prefers_new_photos_and_limits_reused(project, cfg, mock_llm):
    d = cli.cmd_write(write_args(project), cfg)
    post, meta = load_draft(d)
    # P1 = 이번 글 새 사진, P2/P3 = 예전 사진 → 예전 사진은 1장만 남는다
    assert "P1:" in mock_llm["user"] and "재사용" in mock_llm["user"]
    assert [b.photo for b in post.blocks if b.kind == "image"] == ["P1", "P2"]
    assert meta["images"]["P1"]["reused"] is False and meta["images"]["P2"]["reused"] is True
    assert (d / "images/01.jpg").exists() and (d / "images/02.jpg").exists()
    # 마크다운 제거, 태그 정규화(주 키워드 먼저, 중복·# 제거)
    assert [b.text for b in post.blocks if b.kind == "heading"] == ["맛은 어떤가요"]
    assert post.tags == ["밤라떼", "가을신메뉴", "카페"]
    assert not [l for l in meta["lints"] if l["level"] == "block"]
    assert "발행 전 확인" in (d / "preview.md").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "over, code",
    [
        (dict(title="밤라떼 [확인필요: 가격] 후기"), "needs_check"),
        (dict(blocks=[Block(kind="text", text="밤라떼 나왔어요. 이웃추가 부탁드려요!", photo="")]), "engagement_bait"),
        (dict(claims=[Claim(text="10년 경력 바리스타", source="none")]), "unsourced_claim"),
        (dict(blocks=[Block(kind="image", text="(확인 필요) 사진 설명", photo="P1"),
                      Block(kind="text", text="밤라떼 이야기를 길게 해볼게요. 고소하고 달아요.", photo="")]), "needs_check"),
    ],
)
def test_blocking_lints(project, cfg, mock_llm, over, code):
    mock_llm["post"] = fake_post(**over)
    d = cli.cmd_write(write_args(project), cfg)
    _, meta = load_draft(d)
    assert code in {l["code"] for l in meta["lints"] if l["level"] == "block"}


def _publish_args(**over):
    base = dict(mode=None, ignore_overlap=False, republish=False, debug=False)
    base.update(over)
    return Namespace(**base)


@pytest.fixture
def draft(project, cfg, mock_llm):
    return cli.cmd_write(write_args(project), cfg)


def test_publish_records_ledger_and_blocks_republish(project, cfg, draft, monkeypatch):
    monkeypatch.setattr(publisher, "fill_editor", lambda *a, **k: publisher.Result("published", "https://blog.naver.com/tester/224000000001", "224000000001"))
    cli.cmd_publish(_publish_args(draft=str(draft)), cfg)
    _, meta = load_draft(draft)
    assert meta["status"] == "published" and meta["log_no"] == "224000000001"
    led = Ledger(project / "state/ledger.json")
    assert len(led.data["posts"]) == 1 and len(led.data["photo_uses"]) == 2
    with pytest.raises(SystemExit, match="이미"):
        cli.cmd_publish(_publish_args(draft=str(draft)), cfg)


def test_not_published_leaves_draft_untouched(project, cfg, draft, monkeypatch):
    monkeypatch.setattr(publisher, "fill_editor", lambda *a, **k: publisher.Result("not_published"))
    cli.cmd_publish(_publish_args(draft=str(draft)), cfg)
    assert load_draft(draft)[1]["status"] == "draft"
    assert Ledger(project / "state/ledger.json").data["posts"] == []


def test_unknown_result_is_recorded_conservatively(project, cfg, draft, monkeypatch):
    monkeypatch.setattr(publisher, "fill_editor", lambda *a, **k: publisher.Result("unknown"))
    cli.cmd_publish(_publish_args(draft=str(draft)), cfg)
    assert load_draft(draft)[1]["status"] == "unknown"
    assert len(Ledger(project / "state/ledger.json").data["photo_uses"]) == 2


def test_schedule_limit_cannot_be_bypassed(project, cfg, draft, monkeypatch):
    led = Ledger(project / "state/ledger.json")
    led.record_post(at=(datetime.now() - timedelta(hours=2)).isoformat(), status="published", title="x")
    led.save()
    monkeypatch.setattr(publisher, "fill_editor", lambda *a, **k: pytest.fail("열면 안 됨"))
    with pytest.raises(SystemExit, match="시간"):
        cli.cmd_publish(_publish_args(draft=str(draft), ignore_overlap=True, republish=True), cfg)


def test_auto_publish_requires_opt_in(project, cfg, draft, monkeypatch):
    monkeypatch.setattr(publisher, "fill_editor", lambda *a, **k: pytest.fail("열면 안 됨"))
    with pytest.raises(SystemExit, match="allow_auto_publish"):
        cli.cmd_publish(_publish_args(draft=str(draft), mode="publish"), cfg)


def test_publish_relints_after_manual_edit(project, cfg, draft, monkeypatch):
    data = json.loads((draft / "post.json").read_text(encoding="utf-8"))
    data["post"]["blocks"].append({"kind": "text", "text": "서로이웃 환영해요!", "photo": ""})
    (draft / "post.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(publisher, "fill_editor", lambda *a, **k: pytest.fail("열면 안 됨"))
    with pytest.raises(SystemExit, match="이웃"):
        cli.cmd_publish(_publish_args(draft=str(draft)), cfg)


def test_check_halts_after_consecutive_missing(project, cfg, monkeypatch):
    led = Ledger(project / "state/ledger.json")
    for i in range(2):
        led.record_post(at=(datetime.now() - timedelta(days=3 - i)).isoformat(), status="published", title=f"t{i}", indexed=None)
    led.save()
    monkeypatch.setattr("builtins.input", lambda *_: "n")
    cli.cmd_check(Namespace(resume=False), cfg)
    assert Ledger(project / "state/ledger.json").data["halted"]
    cli.cmd_check(Namespace(resume=True), cfg)
    assert not Ledger(project / "state/ledger.json").data["halted"]
