"""직원용 웹 화면. 가게 컴퓨터에서만 열리도록 127.0.0.1 로만 띄운다.

화면에서 하는 일은 명령어(cli.py)와 똑같다. 같은 검사·같은 발행 한도·같은 기록을 쓴다.
"""

from __future__ import annotations

from argparse import Namespace
from datetime import datetime
from pathlib import Path

from flask import Flask, abort, flash, jsonify, redirect, render_template, request, send_from_directory, url_for

from blogbot import cli
from blogbot.config import load_config, paths
from blogbot.corpus import PHOTO_EXTS
from blogbot.web import jobs

KIND_LABELS = {"experience": "경험 글", "info": "정보 글", "promo": "소개 글"}
STATUS_LABELS = {
    "draft": "초안", "published": "발행됨", "scheduled": "예약됨", "unknown": "확인 필요",
    "saved_to_naver_draft": "네이버 임시저장", "not_published": "초안",
}


def create_app(config_path: str = "config.yaml") -> Flask:
    app = Flask(__name__)
    app.secret_key = "blogbot-local"  # 로컬 전용 화면의 알림 메시지용
    app.config["BLOGBOT_CONFIG"] = str(Path(config_path).resolve())

    def cfg() -> dict:
        # 매 요청마다 읽어서, 사장님이 config.yaml 을 고치면 바로 반영되게 한다
        return load_config(app.config["BLOGBOT_CONFIG"])

    def draft_dir(name: str) -> Path:
        cfg()
        root = paths().drafts.resolve()
        d = (root / name).resolve()
        if d.parent != root or not (d / "post.json").exists():
            abort(404)
        return d

    def start_job(kind: str, title: str, fn):
        try:
            job = jobs.start(kind, title, fn)
        except jobs.Busy as e:
            flash(str(e))
            running = jobs.active()
            return redirect(url_for("job_page", job_id=running.id) if running else url_for("home"))
        return redirect(url_for("job_page", job_id=job.id))

    @app.context_processor
    def globals_():
        return {"active_job": jobs.active(), "status_labels": STATUS_LABELS, "kind_labels": KIND_LABELS}

    # ---- 홈 --------------------------------------------------------------
    @app.get("/")
    def home():
        from blogbot.photos import Ledger
        from blogbot.writer import load_draft

        c = cfg()
        P = paths()
        ledger = Ledger(P.ledger)
        drafts = []
        if P.drafts.exists():
            for d in sorted(P.drafts.iterdir(), reverse=True):
                if (d / "post.json").exists():
                    post, meta = load_draft(d)
                    drafts.append({
                        "name": d.name, "title": post.title, "status": meta.get("status", "draft"),
                        "blocks": sum(l["level"] == "block" for l in meta.get("lints", [])),
                        "warns": sum(l["level"] == "warn" for l in meta.get("lints", [])),
                        "created": (meta.get("created_at") or "")[:16].replace("T", " "),
                        "url": meta.get("url", ""),
                    })
        setup = {
            "config": bool(c.get("naver", {}).get("blog_id")) and c["naver"]["blog_id"] != "my_blog_id",
            "style": P.style_profile.exists(),
            "login": P.browser_profile.exists() and any(P.browser_profile.iterdir()),
        }
        return render_template(
            "home.html", drafts=drafts[:30], setup=setup,
            schedule_reason=ledger.schedule_block_reason(c.get("schedule", {})),
            halted=ledger.data.get("halted", ""), pending=len(cli.pending_checks(ledger)),
            mode=c.get("naver", {}).get("mode", "review"),
        )

    # ---- 새 글 ------------------------------------------------------------
    @app.get("/write")
    def write_form():
        return render_template("write.html", form={})

    @app.post("/write")
    def write_submit():
        c = cfg()
        f = request.form
        form = {k: f.get(k, "").strip() for k in ("topic", "keyword", "sub_keywords", "notes", "kind")}
        missing = [label for key, label in (("topic", "주제"), ("keyword", "주 키워드"), ("notes", "이번 글 메모")) if not form[key]]
        if missing:
            flash(f"{', '.join(missing)}을(를) 입력해 주세요.")
            return render_template("write.html", form=form), 400

        photo_dir = None
        files = [x for x in request.files.getlist("photos") if x and x.filename]
        if files:
            photo_dir = paths().root / "data" / "uploads" / datetime.now().strftime("%Y%m%d-%H%M%S")
            photo_dir.mkdir(parents=True, exist_ok=True)
            for i, file in enumerate(files, start=1):
                ext = Path(file.filename).suffix.lower()
                if ext in PHOTO_EXTS:
                    file.save(photo_dir / f"{i:02d}{ext}")

        args = Namespace(
            topic=form["topic"], keyword=form["keyword"], sub_keywords=form["sub_keywords"], notes=form["notes"],
            notes_file=None, photos=str(photo_dir) if photo_dir else None,
            kind=form["kind"] if form["kind"] in KIND_LABELS else "experience", allow_empty_notes=False,
        )
        return start_job("write", f"글쓰기: {form['topic']}", lambda: cli.cmd_write(args, c).name)

    # ---- 초안 --------------------------------------------------------------
    @app.get("/drafts/<name>")
    def draft_page(name):
        from blogbot.writer import load_draft

        d = draft_dir(name)
        post, meta = load_draft(d)
        return render_template("draft.html", name=name, post=post, meta=meta,
                               blocking=[l for l in meta.get("lints", []) if l["level"] == "block"],
                               warnings=[l for l in meta.get("lints", []) if l["level"] == "warn"])

    @app.get("/drafts/<name>/images/<path:filename>")
    def draft_image(name, filename):
        return send_from_directory(draft_dir(name) / "images", filename)

    @app.post("/drafts/<name>/save")
    def draft_save(name):
        from blogbot.writer import load_draft, save_draft

        c = cfg()
        d = draft_dir(name)
        post, meta = load_draft(d)
        if meta.get("status") not in ("draft", "not_published", None):
            flash("이미 올린 글은 여기서 고칠 수 없습니다. 네이버에서 직접 수정하세요.")
            return redirect(url_for("draft_page", name=name))
        post.title = request.form.get("title", post.title).strip()
        keep = []
        for i, b in enumerate(post.blocks):
            if request.form.get(f"remove_{i}"):
                continue
            if b.kind != "divider":
                b.text = request.form.get(f"block_{i}", b.text).replace("\r\n", "\n").strip()
            keep.append(b)
        post.blocks = keep
        post.tags = [t.strip() for t in request.form.get("tags", "").split(",") if t.strip()]
        save_draft(d, post, meta)
        with _quiet():
            cli.cmd_lint(Namespace(draft=str(d)), c)
        flash("저장하고 다시 검사했습니다.")
        return redirect(url_for("draft_page", name=name))

    @app.post("/drafts/<name>/publish")
    def draft_publish(name):
        c = cfg()
        d = draft_dir(name)
        args = Namespace(draft=str(d), mode=None, ignore_overlap=bool(request.form.get("ignore_overlap")),
                         republish=False, debug=bool(request.form.get("debug")))

        def run():
            cli.cmd_publish(args, c)
            return name

        return start_job("publish", f"에디터에 올리기: {name}", run)

    # ---- 작업 진행 ------------------------------------------------------------
    @app.get("/jobs/<int:job_id>")
    def job_page(job_id):
        job = jobs.JOBS.get(job_id) or abort(404)
        return render_template("job.html", job=job)

    @app.get("/jobs/<int:job_id>.json")
    def job_json(job_id):
        job = jobs.JOBS.get(job_id) or abort(404)
        return jsonify(job.to_dict())

    @app.post("/jobs/<int:job_id>/answer")
    def job_answer(job_id):
        job = jobs.JOBS.get(job_id) or abort(404)
        job.answer(request.form.get("answer", ""))
        return redirect(url_for("job_page", job_id=job_id))

    # ---- 검색 확인 -------------------------------------------------------------
    @app.get("/check")
    def check_page():
        from blogbot.photos import Ledger

        cfg()
        ledger = Ledger(paths().ledger)
        items = [{"i": i, "title": p["title"], "at": p["at"][:16].replace("T", " "), "url": cli.search_url(p["title"]),
                  "post_url": p.get("url", "")} for i, p in cli.pending_checks(ledger)]
        return render_template("check.html", items=items, halted=ledger.data.get("halted", ""), help=cli.HALT_HELP)

    @app.post("/check")
    def check_submit():
        from blogbot.photos import Ledger

        c = cfg()
        ledger = Ledger(paths().ledger)
        valid = {i for i, _ in cli.pending_checks(ledger)}
        answers = {}
        for key, value in request.form.items():
            if key.startswith("post_") and value in ("y", "n"):
                i = int(key[5:])
                if i in valid:
                    answers[i] = value == "y"
        reason = cli.record_checks(ledger, answers, c)
        flash(f"⚠ {reason}. 발행을 멈췄습니다." if reason else f"{len(answers)}개 기록했습니다.")
        return redirect(url_for("check_page"))

    @app.post("/check/resume")
    def check_resume():
        from blogbot.photos import Ledger

        cfg()
        cli.resume_publishing(Ledger(paths().ledger))
        flash("발행 중단을 해제했습니다.")
        return redirect(url_for("check_page"))

    # ---- 설정 (학습·사진·로그인) ---------------------------------------------------
    @app.get("/setup")
    def setup_page():
        from blogbot.photos import load_json

        c = cfg()
        P = paths()
        return render_template(
            "setup.html", c=c, config_path=app.config["BLOGBOT_CONFIG"],
            manuscripts=P.manuscripts, photos=P.photos, new_photos=P.root / "data" / "new_photos",
            n_photos=len(load_json(P.photo_index, {})), has_style=P.style_profile.exists(),
        )

    @app.post("/setup/<action>")
    def setup_action(action):
        c = cfg()
        if action == "learn":
            return start_job("learn", "문체 학습", lambda: cli.cmd_learn(Namespace(skip_photos=False), c))
        if action == "photos":
            return start_job("photos", "사진 분석", lambda: cli.cmd_photos(Namespace(), c))
        if action == "login":
            return start_job("login", "네이버 로그인", lambda: cli.cmd_login(Namespace(), c))
        abort(404)

    return app


class _quiet:
    """cmd_lint 의 콘솔 출력이 서버 창을 어지럽히지 않게."""

    def __enter__(self):
        import io
        from contextlib import redirect_stdout

        self._ctx = redirect_stdout(io.StringIO())
        return self._ctx.__enter__()

    def __exit__(self, *exc):
        return self._ctx.__exit__(*exc)


def serve(config_path: str, port: int = 8765, open_browser: bool = True) -> None:
    import threading
    import webbrowser

    app = create_app(config_path)
    url = f"http://127.0.0.1:{port}"
    print(f"블로그 도우미 화면: {url}  (이 창을 닫으면 프로그램이 꺼집니다)")
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False, threaded=True)
