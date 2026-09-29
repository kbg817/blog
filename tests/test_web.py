import io
import time
from datetime import datetime, timedelta

import pytest
import yaml
from PIL import Image

from blogbot import llm, publisher
from blogbot.photos import Ledger
from blogbot.web import jobs
from blogbot.web.app import create_app
from tests.test_flow import fake_post


@pytest.fixture
def client(project, cfg, monkeypatch):
    (project / "config.yaml").write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")

    def parse(**kw):
        name = kw["output"].__name__
        if name == "PhotoDescription":
            return kw["output"](description="사진", keywords=["카페"], specific_details=[], orientation="가로")
        return fake_post()

    monkeypatch.setattr(llm, "parse", parse)
    jobs.JOBS.clear()
    app = create_app(str(project / "config.yaml"))
    app.testing = True
    return app.test_client()


def wait(client, job_id, until=("done", "error"), timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        j = client.get(f"/jobs/{job_id}.json").get_json()
        if j["status"] in until:
            return j
        time.sleep(0.1)
    raise AssertionError(f"작업이 끝나지 않음: {j}")


def _jpeg(color="orange"):
    buf = io.BytesIO()
    Image.new("RGB", (1200, 900), color).save(buf, "JPEG")
    buf.seek(0)
    return buf


def write_draft(client):
    r = client.post("/write", data={
        "topic": "가을 신메뉴", "keyword": "밤라떼", "sub_keywords": "가을 신메뉴", "kind": "experience",
        "notes": "밤라떼 출시, 5,500원, 고소하고 은근히 달다",
        "photos": [(_jpeg(), "a.jpg"), (_jpeg("brown"), "b.jpg")],
    }, content_type="multipart/form-data")
    assert r.status_code == 302
    job_id = int(r.headers["Location"].rsplit("/", 1)[1])
    j = wait(client, job_id)
    assert j["status"] == "done", j
    return j["result"]


def test_pages_render(client):
    for url in ("/", "/write", "/check", "/setup"):
        r = client.get(url)
        assert r.status_code == 200, url
    assert "처음 준비" in client.get("/").get_data(as_text=True)


def test_write_requires_notes(client):
    r = client.post("/write", data={"topic": "x", "keyword": "y", "notes": ""})
    assert r.status_code == 400 and "이번 글 메모" in r.get_data(as_text=True)


def test_write_then_edit_relints(client):
    name = write_draft(client)
    page = client.get(f"/drafts/{name}").get_data(as_text=True)
    assert "에디터에 올리기" in page and "/images/01.jpg" in page
    assert client.get(f"/drafts/{name}/images/01.jpg").status_code == 200

    # 문단에 이웃추가 요청을 넣으면 다시 검사해서 올리기 버튼이 막힌다
    r = client.post(f"/drafts/{name}/save", data={"title": "밤라떼 출시 후기", "block_0": "이웃추가 해주세요!", "tags": "밤라떼, 카페"})
    assert r.status_code == 302
    page = client.get(f"/drafts/{name}").get_data(as_text=True)
    assert "이웃추가" in page and "올리기 전에 고쳐야 할 것" in page
    assert 'type="submit" disabled' in page


def test_publish_asks_staff_and_records_answer(client, project, monkeypatch):
    name = write_draft(client)

    def fake_fill(post, draft_dir, images, cfg, profile, debug=False):
        ans = publisher.ask("다 끝났나요?", publisher.DONE_CHOICES)
        return publisher.Result({"p": "published", "r": "scheduled"}.get(ans, "not_published"))

    monkeypatch.setattr(publisher, "fill_editor", fake_fill)
    r = client.post(f"/drafts/{name}/publish", data={})
    job_id = int(r.headers["Location"].rsplit("/", 1)[1])
    j = wait(client, job_id, until=("waiting",))
    assert j["question"]["choices"] == publisher.DONE_CHOICES

    # 기다리는 동안 다른 작업은 시작되지 않는다
    r = client.post("/setup/photos")
    assert r.headers["Location"].endswith(f"/jobs/{job_id}")

    client.post(f"/jobs/{job_id}/answer", data={"answer": "r"})
    assert wait(client, job_id)["status"] == "done"
    assert Ledger(project / "state/ledger.json").data["posts"][0]["status"] == "scheduled"
    assert "예약됨" in client.get("/").get_data(as_text=True)
    assert publisher.ask is publisher._terminal_ask  # 작업이 끝나면 원래대로


def test_publish_blocked_by_schedule_shows_reason(client, project):
    name = write_draft(client)
    led = Ledger(project / "state/ledger.json")
    led.record_post(at=(datetime.now() - timedelta(hours=1)).isoformat(), status="published", title="x")
    led.save()
    assert "지금은 올릴 수 없어요" in client.get("/").get_data(as_text=True)
    r = client.post(f"/drafts/{name}/publish", data={})
    j = wait(client, int(r.headers["Location"].rsplit("/", 1)[1]))
    assert j["status"] == "error" and "시간" in j["error"]


def test_check_page_records_and_halts(client, project):
    led = Ledger(project / "state/ledger.json")
    for i in range(2):
        led.record_post(at=(datetime.now() - timedelta(days=3)).isoformat(), status="published", title=f"글{i}", indexed=None)
    led.save()
    page = client.get("/check").get_data(as_text=True)
    assert "search.naver.com" in page and "글0" in page
    client.post("/check", data={"post_0": "n", "post_1": "n"})
    assert Ledger(project / "state/ledger.json").data["halted"]
    assert "발행이 멈춰" in client.get("/").get_data(as_text=True)
    client.post("/check/resume")
    assert not Ledger(project / "state/ledger.json").data["halted"]


def test_unknown_draft_is_404(client):
    assert client.get("/drafts/../../etc").status_code == 404
    assert client.get("/drafts/nope").status_code == 404
