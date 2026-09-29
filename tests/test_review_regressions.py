"""코드 리뷰에서 확인된 버그들의 회귀 테스트."""

from argparse import Namespace
from datetime import datetime, timedelta

import pytest

from blogbot import cli, llm
from blogbot.corpus import split_sentences
from blogbot.photos import Ledger
from blogbot.writer import ENGAGEMENT_BAIT, Block, Claim, Post, Revision, Edit, lint_post, revise_post
from tests.conftest import make_profile


def test_scheduled_posts_count_toward_limits(tmp_path):
    led = Ledger(tmp_path / "l.json")
    led.record_post(status="scheduled", title="예약글")
    assert led.schedule_block_reason({}) != ""


def test_archive_photo_not_double_counted_after_move(tmp_path):
    led = Ledger(tmp_path / "l.json")
    led.data["photo_uses"]["h"] = [{"at": (datetime.now() - timedelta(days=200)).isoformat(), "draft": "d"}]
    assert led.use_count("h", {"source": "archive"}) == 1
    assert led.photo_available("h", {"source": "archive"}, 90, 2)


def test_split_sentences_keeps_mid_sentence_words():
    assert split_sentences("무엇보다 중요한 건 처음 마음 그대로라는 점이에요.") == ["무엇보다 중요한 건 처음 마음 그대로라는 점이에요."]
    assert len(split_sentences("오늘 날씨가 정말 좋았어요 그래서 산책을 다녀왔답니다 다음엔 친구랑 가보려구요")) == 3


@pytest.mark.parametrize("text, bait", [
    ("많은 분들이 공감해 주셨어요.", False),
    ("궁금한 점은 댓글 주시면 바로 답변 드려요.", False),
    ("공감이 되더라고요", False),
    ("공감 꾹 눌러주세요!", True),
    ("이웃추가 해주시면 소식 받아보실 수 있어요", True),
    ("댓글 남겨주신 분께 추첨으로 쿠폰 드려요", True),
])
def test_engagement_bait_regex(text, bait):
    assert bool(ENGAGEMENT_BAIT.search(text)) is bait


def _post(blocks, tags=("밤라떼",)):
    return Post(search_intent="-", new_info=[], title="밤라떼 출시 후기 이야기", blocks=blocks, tags=list(tags),
                claims=[Claim(text="-", source="notes")])


def _ctx(cfg, **over):
    ctx = {"cfg": cfg, "keyword": "밤라떼", "notes": "메모", "overlap": {"max_ratio": 0, "most_similar": "", "copied_sentences": []},
           "ai_tells": [], "legal_watch": [], "stats": {}}
    ctx.update(over)
    return ctx


def test_tag_relevance_check_fires(cfg):
    post = _post([Block(kind="text", text="밤라떼 이야기 " * 30, photo="")],
                 tags=["밤라떼", "전혀무관태그", "제주도여행"])
    msgs = [l["msg"] for l in lint_post(post, _ctx(cfg)) if l["code"] == "tags"]
    assert msgs and "제주도여행" in msgs[0]


def test_editor_skips_empty_edits(cfg, monkeypatch):
    post = _post([Block(kind="text", text="첫 문단", photo=""), Block(kind="text", text="본문", photo="")])
    monkeypatch.setattr(llm, "parse", lambda **kw: Revision(title="", edits=[Edit(block_index=1, new_text="")]))
    post, applied = revise_post(post, [{"level": "warn", "code": "kw_intro", "msg": "x"}], make_profile(), "밤라떼", cfg)
    assert post.blocks[1].text == "본문" and applied == 0


def test_blank_business_fields_do_not_crash(project, cfg):
    cfg["business"].update(call_to_action=None, description=None, facts=None)
    post = _post([Block(kind="text", text="밤라떼 이야기 " * 30, photo="")])
    ctx = cli._lint_ctx(cfg, "밤라떼", "메모", post)
    lint_post(post, ctx)


def test_check_resume_does_not_rehalt_on_old_answers(project, cfg, monkeypatch):
    led = Ledger(project / "state/ledger.json")
    old = (datetime.now() - timedelta(days=5)).isoformat()
    for i in range(2):
        led.record_post(at=old, status="published", title=f"old{i}", indexed=False, checked_at=old)
    led.record_post(at=(datetime.now() - timedelta(days=2)).isoformat(), status="published", title="new", indexed=None)
    led.save()
    cli.cmd_check(Namespace(resume=True), cfg)
    monkeypatch.setattr("builtins.input", lambda *_: "")     # 건너뛰기만
    cli.cmd_check(Namespace(resume=False), cfg)
    assert not Ledger(project / "state/ledger.json").data["halted"]
    monkeypatch.setattr("builtins.input", lambda *_: "n")    # 새 글 1개만 누락 → 아직 중단 아님
    cli.cmd_check(Namespace(resume=False), cfg)
    assert not Ledger(project / "state/ledger.json").data["halted"]


def test_markdown_and_emoji_bullets_are_stripped():
    from blogbot.writer import clean_post

    post = _post([Block(kind="text", text="- 첫째 항목\n• 둘째\n✅ 셋째\n**굵게** 문장", photo="")])
    post = clean_post(post, "밤라떼", {}, set(), 1)
    assert post.blocks[0].text == "첫째 항목\n둘째\n셋째\n굵게 문장"
