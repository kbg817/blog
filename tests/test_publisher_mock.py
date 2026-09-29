"""가짜 SmartEditor 페이지로 publisher 의 입력·발행 흐름을 검증한다 (네이버에 접속하지 않음).

실제 네이버 화면의 셀렉터가 맞는지는 이 테스트로 알 수 없다. 그건 review 모드 첫 실행에서
직접 확인해야 한다. 여기서는 흐름(사진 업로드 대기, 소제목 서식, 발행 설정 확인, 결과 판정,
계정 상태 감지)이 의도대로 동작하는지만 본다.
"""

import json
import os
from pathlib import Path

import pytest
from PIL import Image

from blogbot import publisher
from blogbot.writer import Block, Claim, Post

CHROMIUM = os.environ.get("BLOGBOT_TEST_CHROMIUM", "/opt/pw-browsers/chromium")
MOCK_HTML = (Path(__file__).parent / "mock_editor.html").read_text(encoding="utf-8")
IFRAME_HTML = '<!doctype html><iframe id="mainFrame" src="https://blog.naver.com/PostWriteForm.naver?blogId=tester" style="width:100%;height:900px"></iframe>'

pytestmark = pytest.mark.skipif(
    not Path(CHROMIUM).exists(), reason="테스트용 크로미움이 없습니다 (BLOGBOT_TEST_CHROMIUM)"
)


@pytest.fixture
def harness(monkeypatch, tmp_path):
    state = {"records": [], "variant": "top", "expired": False}

    def handler(route):
        url = route.request.url
        if "/__record" in url:
            state["records"].append(json.loads(route.request.post_data))
            return route.fulfill(status=200, body="ok")
        if "/tester/postwrite" in url:
            if state["expired"]:
                return route.fulfill(
                    status=200, content_type="text/html",
                    body="<script>location.replace('https://nid.naver.com/nidlogin.login')</script>",
                )
            body = IFRAME_HTML if state["variant"] == "iframe" else MOCK_HTML
            if state["variant"] == "strict":
                body = body.replace("<body>", "<body><script>window.STRICT = true</script>", 1)
            return route.fulfill(status=200, content_type="text/html; charset=utf-8", body=body)
        if "PostWriteForm.naver" in url:
            return route.fulfill(status=200, content_type="text/html; charset=utf-8", body=MOCK_HTML)
        if "/tester/224000000001" in url:
            return route.fulfill(status=200, content_type="text/html; charset=utf-8", body="<p>글 보기</p>")
        if "nid.naver.com" in url:
            return route.fulfill(status=200, content_type="text/html; charset=utf-8", body="<p>로그인</p>")
        return route.abort()

    def fake_launch(p, cfg, profile_dir):
        ctx = p.chromium.launch_persistent_context(
            str(profile_dir), headless=True, executable_path=CHROMIUM, locale="ko-KR"
        )
        ctx.route("**/*", handler)
        return ctx

    monkeypatch.setattr(publisher, "_launch", fake_launch)

    draft = tmp_path / "draft"
    (draft / "images").mkdir(parents=True)
    for name, color in (("01.jpg", "orange"), ("02.jpg", "brown")):
        Image.new("RGB", (400, 300), color).save(draft / "images" / name)
    state["draft"] = draft
    state["profile"] = tmp_path / "profile"
    return state


POST = Post(
    search_intent="-",
    new_info=["-"],
    title="밤라떼 출시 후기",
    blocks=[
        Block(kind="text", text="제가 운영하는 카페 이야기예요.", photo=""),
        Block(kind="image", text="", photo="P1"),
        Block(kind="heading", text="맛은 어떤가요", photo=""),
        Block(kind="text", text="고소하고 은근히 달아요.\n따뜻하게 드세요.", photo=""),
        Block(kind="image", text="오후에 찍은 한 잔", photo="P2"),
    ],
    tags=["밤라떼", "가을신메뉴"],
    claims=[Claim(text="-", source="notes")],
)
IMAGES = {"P1": {"file": "01.jpg", "hash": "a"}, "P2": {"file": "02.jpg", "hash": "b"}}


def _cfg(mode, **naver):
    return {"naver": {"blog_id": "tester", "mode": mode, "category": "카페 이야기", **naver}}


@pytest.mark.parametrize("variant", ["top", "iframe"])
def test_publish_mode_fills_everything_and_detects_post_url(harness, variant):
    harness["variant"] = variant
    res = publisher.fill_editor(POST, harness["draft"], IMAGES, _cfg("publish"), harness["profile"])
    assert res.status == "published" and res.log_no == "224000000001"
    rec = harness["records"][-1]
    assert rec["action"] == "publish"
    assert rec["title"] == "밤라떼 출시 후기"
    kinds = [c["kind"] for c in rec["components"] if not (c["kind"] == "text" and not c.get("text"))]
    assert kinds == ["text", "image", "heading", "text", "text", "image", "text"]
    assert all(c["src"].startswith("https://") for c in rec["components"] if c["kind"] == "image")
    assert [c["file"] for c in rec["components"] if c["kind"] == "image"] == ["01.jpg", "02.jpg"]
    assert rec["tags"] == ["밤라떼", "가을신메뉴"]
    assert rec["category"] == "카페 이야기"
    assert rec["public"] and rec["search"]  # 검색허용이 꺼져 있던 것을 켜고 발행


def test_draft_mode_saves_without_publishing(harness):
    harness["variant"] = "iframe"
    res = publisher.fill_editor(POST, harness["draft"], IMAGES, _cfg("draft"), harness["profile"])
    assert res.status == "saved_to_naver_draft"
    assert [r["action"] for r in harness["records"]] == ["save"]


def test_review_mode_waits_for_human_and_does_not_click_publish(harness, monkeypatch):
    answers = iter(["", "n"])  # Enter(확인) → 발행 주소 없음 → 'n' 발행 안 함
    monkeypatch.setattr("builtins.input", lambda *_: next(answers))
    res = publisher.fill_editor(POST, harness["draft"], IMAGES, _cfg("review"), harness["profile"])
    assert res.status == "not_published"
    assert harness["records"] == []  # 최종 발행 버튼을 누르지 않음


def test_expired_session_stops_without_retry(harness):
    harness["expired"] = True
    with pytest.raises(publisher.Blocked, match="로그인이 만료"):
        publisher.fill_editor(POST, harness["draft"], IMAGES, _cfg("publish"), harness["profile"])
    assert harness["records"] == []


def test_falls_back_to_per_key_typing_when_editor_drops_bulk_input(harness):
    harness["variant"] = "strict"
    res = publisher.fill_editor(POST, harness["draft"], IMAGES, _cfg("draft"), harness["profile"])
    assert res.status == "saved_to_naver_draft"
    assert any("한 글자씩" in n for n in res.notes)
    rec = harness["records"][-1]
    assert rec["title"] == "밤라떼 출시 후기"
    assert "고소하고 은근히 달아요." in [c.get("text") for c in rec["components"]]
