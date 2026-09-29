"""명령행 진입점: python -m blogbot <명령>"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from blogbot.config import load_config, paths

PUBLISHED_STATES = ("published", "scheduled", "unknown", "saved_to_naver_draft")


# ---- 공통 --------------------------------------------------------------------


def _own_posts(exclude: Path | None = None) -> list[dict]:
    """이 프로그램으로 이미 올린(또는 올렸을 수 있는) 글들. 유사도 비교 대상."""
    from blogbot.writer import body_text, load_draft

    root = paths().drafts
    out = []
    if not root.exists():
        return out
    for d in sorted(root.iterdir()):
        if not (d / "post.json").exists() or (exclude and d.resolve() == exclude.resolve()):
            continue
        post, meta = load_draft(d)
        if meta.get("status") in PUBLISHED_STATES:
            out.append(
                {
                    "dir": d.name,
                    "title": post.title,
                    "headings": [b.text for b in post.blocks if b.kind == "heading"],
                    "text": body_text(post),
                }
            )
    return out


def _lint_ctx(cfg: dict, keyword: str, notes: str, post, exclude: Path | None = None, extra: dict | None = None) -> dict:
    from blogbot.corpus import Manuscript, load_manuscripts, overlap_report
    from blogbot.photos import load_json
    from blogbot.style import load_profile
    from blogbot.writer import DEFAULT_AI_TELLS, DEFAULT_LEGAL_WATCH, body_text

    P = paths()
    wcfg, biz = cfg.get("writing", {}), cfg.get("business", {})
    docs = load_manuscripts(P.manuscripts)
    own = _own_posts(exclude)
    corpus = docs + [Manuscript(f"(이 프로그램으로 올린 글) {o['title']}", o["text"]) for o in own]
    try:
        profile = load_profile(P.style_profile)
        signatures = list(profile.signature_phrases)
    except SystemExit:
        signatures = []
    whitelist = [biz.get("call_to_action", ""), biz.get("disclosure", "")] + list(biz.get("facts", []) or []) + signatures
    ctx = {
        "cfg": cfg,
        "keyword": keyword,
        "notes": notes,
        "overlap": overlap_report(body_text(post), corpus, whitelist),
        "previous_titles": [d.title for d in docs] + [o["title"] for o in own],
        "recent_posts": own[-10:],
        "stats": load_json(P.style_stats, {}),
        "signatures": signatures,
        "ai_tells": list(DEFAULT_AI_TELLS) + list(wcfg.get("banned_phrases", []) or []),
        "legal_watch": list(DEFAULT_LEGAL_WATCH) + list(wcfg.get("legal_watchlist", []) or []),
        "known_facts": "\n".join([notes, keyword, *(biz.get("facts", []) or []), biz.get("description", "")]),
    }
    ctx.update(extra or {})
    return ctx


def _photo_sources(cfg: dict) -> list[tuple[Path, str]]:
    """(폴더, source) 목록. data/photos = 예전에 블로그에 올렸던 사진, data/new_photos = 아직 안 올린 사진."""
    P = paths()
    archive_src = "archive" if cfg.get("images", {}).get("archive_already_published", True) else "new"
    return [(P.photos, archive_src), (P.root / "data" / "new_photos", "new")]


# ---- 명령 --------------------------------------------------------------------


def cmd_learn(args, cfg) -> None:
    from blogbot.corpus import load_manuscripts, style_stats
    from blogbot.photos import save_json
    from blogbot.style import build_profile, save_profile

    P = paths()
    docs = load_manuscripts(P.manuscripts)
    if not docs:
        raise SystemExit(f"{P.manuscripts} 에 원고가 없습니다. (.txt / .md / .docx)")
    stats = style_stats([d.text for d in docs])
    save_json(P.style_stats, stats)
    print(f"원고 {len(docs)}편 통계 저장: {P.style_stats}")
    w = cfg.get("writing", {})
    print("문체를 분석합니다...")
    profile = build_profile(
        docs, w.get("learn_model", w.get("model", "claude-opus-5-5")), w.get("learn_effort", "high"),
        int(w.get("learn_max_docs", 60)),
    )
    save_profile(profile, P.style_profile)
    print(f"문체 프로필 저장: {P.style_profile}")
    if not args.skip_photos:
        cmd_photos(args, cfg)


def cmd_photos(args, cfg) -> None:
    from blogbot.corpus import list_photos
    from blogbot.photos import index_photos

    P = paths()
    model = cfg.get("writing", {}).get("photo_model", cfg.get("writing", {}).get("model", "claude-opus-5-5"))
    for folder, source in _photo_sources(cfg):
        photos = list_photos(folder)
        if photos:
            print(f"{folder.name}: 사진 {len(photos)}장 (새 사진만 분석)")
            index_photos(photos, P.photo_index, model, source)
    print(f"사진 인덱스: {P.photo_index}")


def cmd_write(args, cfg) -> Path:
    from blogbot.corpus import list_photos, load_manuscripts
    from blogbot.images import process_image
    from blogbot.photos import Ledger, existing_path, index_photos, load_json
    from blogbot.style import load_profile
    from blogbot.writer import (
        add_disclosure, body_text, chars_no_space, lint_post, new_draft_dir, revise_post, save_draft, write_post,
    )

    P = paths()
    icfg, wcfg = cfg.get("images", {}), cfg.get("writing", {})
    notes = args.notes or ""
    if getattr(args, "notes_file", None):
        notes = (notes + "\n" + Path(args.notes_file).read_text(encoding="utf-8")).strip()
    if not notes.strip() and not args.allow_empty_notes:
        raise SystemExit(
            "--notes 로 이번 글만의 새 정보(직접 겪은 일, 날짜, 가격, 느낀 점 등)를 적어 주세요.\n"
            "새 경험 정보가 없는 글은 네이버 노출에 불리하고, AI 가 경험을 지어낼 위험이 있습니다.\n"
            "그래도 진행하려면 --allow-empty-notes"
        )

    docs = load_manuscripts(P.manuscripts)
    profile = load_profile(P.style_profile)
    stats = load_json(P.style_stats, {})
    ledger = Ledger(P.ledger)
    model = wcfg.get("photo_model", wcfg.get("model", "claude-opus-5-5"))

    # 이번 글 전용 사진(--photos) → 새 사진 폴더 → 예전 사진 순으로 우선
    index = load_json(P.photo_index, {})
    post_photo_hashes: set[str] = set()
    if args.photos:
        from blogbot.photos import file_hash

        files = list_photos(Path(args.photos))
        if files:
            index = index_photos(files, P.photo_index, model, "new")
            post_photo_hashes = {file_hash(f) for f in files}
    for folder, source in _photo_sources(cfg):
        files = list_photos(folder)
        if files:
            index = index_photos(files, P.photo_index, model, source)

    archive_used = bool(icfg.get("archive_already_published", True))
    cooldown, max_uses = int(icfg.get("reuse_cooldown_days", 90)), int(icfg.get("max_uses_per_photo", 2))
    available = {
        h: e for h, e in index.items()
        if existing_path(e) and ledger.photo_available(h, e, cooldown, max_uses, archive_used)
    }
    ranked = sorted(
        available.items(),
        key=lambda kv: (kv[0] not in post_photo_hashes, ledger.use_count(kv[0], kv[1], archive_used) > 0),
    )[: int(wcfg.get("max_photo_candidates", 30))]
    photo_ids = {f"P{i}": e for i, (_, e) in enumerate(ranked, start=1)}
    id_to_hash = {f"P{i}": h for i, (h, _) in enumerate(ranked, start=1)}
    reused_ids = {pid for pid, h in id_to_hash.items() if ledger.use_count(h, index[h], archive_used) > 0}
    if not any(pid not in reused_ids for pid in photo_ids):
        print("⚠ 새 사진이 없습니다. 예전 사진은 글당 최대 "
              f"{icfg.get('max_reused_per_post', 1)}장만 씁니다. --photos 로 새 사진 폴더를 주는 것을 권장합니다.")

    own = _own_posts()
    print(f"'{args.topic}' 글을 작성합니다... (사진 후보 {len(photo_ids)}장, 그중 재사용 {len(reused_ids)}장)")
    post = write_post(
        topic=args.topic, keyword=args.keyword, sub_keywords=_split(args.sub_keywords), notes=notes, kind=args.kind,
        profile=profile, stats=stats, docs=docs, photo_ids=photo_ids, reused_ids=reused_ids, recent_posts=own, cfg=cfg,
    )
    post = add_disclosure(post, cfg.get("business", {}))

    def lint_extra():
        used = [b.photo for b in post.blocks if b.kind == "image"]
        year_ago = (datetime.now() - timedelta(days=365)).isoformat()
        return {
            "reused_in_post": sum(p in reused_ids for p in used),
            "stale_photos": sum(1 for p in used if (photo_ids[p].get("taken_at") or "9999") < year_ago),
        }

    ctx = _lint_ctx(cfg, args.keyword, notes, post, extra=lint_extra())
    lints = lint_post(post, ctx)
    revision = None
    for _ in range(int(wcfg.get("review_passes", 1))):
        before = len(lints)
        post, applied = revise_post(post, lints, profile, args.keyword, cfg)
        if not applied:
            break
        ctx = _lint_ctx(cfg, args.keyword, notes, post, extra=lint_extra())
        lints = lint_post(post, ctx)
        revision = {"applied": applied, "before": before, "after": len(lints)}
        print(f"  편집 패스: {applied}곳 수정 (지적 {before}개 → {len(lints)}개)")

    d = new_draft_dir(post, P.drafts)
    meta = {
        "status": "draft",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "topic": args.topic,
        "keyword": args.keyword,
        "sub_keywords": _split(args.sub_keywords),
        "kind": args.kind,
        "notes": notes,
        "images": {},
        "lints": lints,
        "overlap": ctx["overlap"],
        "chars_no_space": chars_no_space(body_text(post)),
        "revision": revision,
    }
    save_draft(d, post, meta)  # 사진 가공 전에 먼저 저장 (사진에 문제가 있어도 글은 남도록)

    images: dict[str, dict] = {}
    keep = []
    for b in post.blocks:
        if b.kind != "image":
            keep.append(b)
            continue
        h = id_to_hash[b.photo]
        name = f"{len(images) + 1:02d}.jpg"
        try:
            _, warns = process_image(existing_path(index[h]), d / "images" / name, icfg)
        except Exception as e:
            lints.append({"level": "warn", "code": "image_error", "msg": f"사진 처리 실패로 뺐습니다 ({b.photo}): {e}"})
            continue
        lints += [{"level": "warn", "code": "image", "msg": w} for w in warns]
        images[b.photo] = {"file": name, "hash": h, "reused": b.photo in reused_ids}
        keep.append(b)
    post.blocks = keep
    meta.update(images=images, lints=lints)
    save_draft(d, post, meta)
    _print_lints(lints)
    print(f"\n초안 저장: {d}\n  미리보기: {d / 'preview.md'}")
    return d


def cmd_lint(args, cfg) -> None:
    """post.json 을 직접 고친 뒤 다시 검사하고 preview.md 를 갱신한다."""
    from blogbot.writer import body_text, chars_no_space, lint_post, load_draft, normalize_tags, save_draft

    d = Path(args.draft)
    post, meta = load_draft(d)
    post.tags = normalize_tags(post.tags, meta.get("keyword", ""))
    images = meta.get("images", {})
    extra = {"reused_in_post": sum(1 for v in images.values() if v.get("reused"))}
    ctx = _lint_ctx(cfg, meta.get("keyword", ""), meta.get("notes", ""), post, exclude=d, extra=extra)
    # 사진 가공 단계에서 나온 경고는 다시 계산할 수 없으므로 그대로 유지
    kept = [l for l in meta.get("lints", []) if l["code"] in ("image", "image_error", "old_photos")]
    meta["lints"] = lint_post(post, ctx) + kept
    meta["overlap"] = ctx["overlap"]
    meta["chars_no_space"] = chars_no_space(body_text(post))
    save_draft(d, post, meta)
    _print_lints(meta["lints"])
    print(f"미리보기 갱신: {d / 'preview.md'}")


def cmd_publish(args, cfg, draft_dir: Path | None = None) -> None:
    from blogbot.photos import Ledger
    from blogbot.publisher import fill_editor
    from blogbot.writer import load_draft, update_meta

    P = paths()
    draft_dir = draft_dir or Path(args.draft)
    ncfg = cfg.setdefault("naver", {})
    if args.mode:
        ncfg["mode"] = args.mode
    mode = ncfg.get("mode", "review")
    if not ncfg.get("blog_id") or ncfg.get("blog_id") == "my_blog_id":
        raise SystemExit("config.yaml 의 naver.blog_id 를 설정해 주세요.")

    # 발행 직전에 다시 검사 (post.json 을 직접 고쳤을 수 있으므로)
    cmd_lint(argparse.Namespace(draft=str(draft_dir)), cfg)
    post, meta = load_draft(draft_dir)

    if meta.get("status") in PUBLISHED_STATES and not args.republish:
        raise SystemExit(
            f"이 초안은 이미 '{meta['status']}' 상태입니다. 같은 글을 두 번 올리면 내 글끼리 유사문서가 됩니다.\n"
            "네이버에서 실제로 안 올라간 것을 확인했다면 --republish"
        )
    blocking = [l for l in meta["lints"] if l["level"] == "block" and not (args.ignore_overlap and l["code"] == "overlap")]
    if blocking:
        raise SystemExit("발행 전에 고쳐야 할 항목:\n" + "\n".join(f"  - {l['msg']}" for l in blocking)
                         + "\npost.json 을 고친 뒤 `python -m blogbot lint <초안>` 으로 다시 확인하세요.")

    ledger = Ledger(P.ledger)
    reason = ledger.schedule_block_reason(cfg.get("schedule", {}))
    if reason:
        raise SystemExit(reason)

    if mode == "publish":
        if not ncfg.get("allow_auto_publish"):
            raise SystemExit(
                "자동 발행(publish 모드)은 config 에서 naver.allow_auto_publish: true 로 직접 켜야 합니다.\n"
                "네이버 이용약관은 자동화된 수단으로 게시물을 게재하는 행위를 금지합니다. review 모드를 권장합니다."
            )
        print("⚠ 자동 발행 모드: 네이버 이용약관상 제재 위험이 있습니다.")

    res = fill_editor(post, draft_dir, meta.get("images", {}), cfg, P.browser_profile, debug=args.debug)
    for n in res.notes:
        print("  ⚠", n)
    print(f"결과: {res.status}" + (f" ({res.url})" if res.url else ""))

    if res.status in PUBLISHED_STATES:
        # 발행 여부가 불확실해도 사진·한도는 보수적으로 '사용함'으로 기록한다.
        ledger.record_photos([v["hash"] for v in meta.get("images", {}).values()], draft_dir.name)
        ledger.record_post(
            draft=draft_dir.name, title=post.title, mode=mode, status=res.status, url=res.url, log_no=res.log_no,
            keyword=meta.get("keyword", ""), indexed=None,
        )
        ledger.save()
        update_meta(draft_dir, status=res.status, url=res.url, log_no=res.log_no, publish_notes=res.notes)
        if res.status == "unknown":
            print("발행 여부를 확인하지 못했습니다. 블로그에서 직접 확인해 주세요 (재발행은 --republish 필요).")
        if res.status == "saved_to_naver_draft":
            print("네이버 임시저장함에 저장했습니다. 거기서 열어 예약 발행하세요.")


def cmd_run(args, cfg) -> None:
    mode = args.mode or cfg.get("naver", {}).get("mode", "review")
    if mode == "publish":
        raise SystemExit("run 은 사람이 검토하는 review/draft 모드에서만 쓸 수 있습니다. write → preview 확인 → publish 순서로 진행하세요.")
    from blogbot.writer import load_draft

    d = cmd_write(args, cfg)
    if any(l["level"] == "block" for l in load_draft(d)[1]["lints"]):
        print("\n발행 전에 고쳐야 할 항목이 있어 여기서 멈춥니다. preview.md 를 확인하세요.")
        return
    if input("\npreview.md 를 확인했나요? 에디터에 올릴까요? [y/N]: ").strip().lower() != "y":
        return
    cmd_publish(args, cfg, draft_dir=d)


def cmd_check(args, cfg) -> None:
    """발행 1~2일 뒤 검색 누락 여부를 사람이 확인하고 기록한다.

    네이버 검색을 프로그램으로 긁지 않는다 (이용약관의 '자동화된 수단으로 검색' 금지).
    대신 검색 주소를 보여주고, 사람이 확인한 결과를 기록한다. 누락이 연속되면 발행을 멈춘다.
    """
    from blogbot.photos import Ledger

    ledger = Ledger(paths().ledger)
    if args.resume:
        ledger.data["halted"] = ""
        ledger.save()
        print("발행 중단을 해제했습니다.")
        return
    now = datetime.now()
    pending = [
        p for p in ledger.data["posts"]
        if p.get("status") in ("published", "scheduled", "unknown") and p.get("indexed") is None
        and now - datetime.fromisoformat(p["at"]) >= timedelta(hours=24)
    ]
    if not pending:
        print("확인할 글이 없습니다 (발행 후 24시간이 지난 글만 확인합니다).")
        return
    print("각 주소를 브라우저에서 열어 '블로그' 탭에 내 글이 보이는지 확인하세요.\n")
    for p in pending:
        url = "https://search.naver.com/search.naver?where=blog&query=" + quote(f'"{p["title"]}"')
        print(f"- {p['title']}\n  {url}")
        ans = input("  검색에 나오나요? [y]나옴 / [n]안 나옴 / [Enter]건너뛰기: ").strip().lower()
        if ans in ("y", "n"):
            p["indexed"] = ans == "y"
            p["checked_at"] = now.isoformat(timespec="seconds")
    checked = [p for p in ledger.data["posts"] if p.get("indexed") is not None]
    streak = int(cfg.get("schedule", {}).get("halt_after_missing", 2))
    if len(checked) >= streak and all(p["indexed"] is False for p in checked[-streak:]):
        ledger.data["halted"] = f"최근 {streak}개 글이 검색에 나오지 않음"
        print(
            f"\n⚠ 최근 {streak}개 글이 검색에서 빠졌습니다. 발행을 멈춥니다.\n"
            "  - 네이버 고객센터 > 검색 > '검색 반영 요청'에서 해당 글을 요청할 수 있습니다.\n"
            "  - 최근 글에 새 경험 정보가 충분했는지, 예전 글·사진과 겹치지 않았는지 점검하세요.\n"
            "  - 정리 후 `python -m blogbot check --resume` 으로 해제합니다."
        )
    ledger.save()


def cmd_login(args, cfg) -> None:
    from blogbot.publisher import login

    login(cfg, paths().browser_profile)


def cmd_drafts(args, cfg) -> None:
    from blogbot.writer import load_draft

    root = paths().drafts
    if not root.exists():
        print("초안이 없습니다.")
        return
    for d in sorted(root.iterdir()):
        if (d / "post.json").exists():
            post, meta = load_draft(d)
            n_block = sum(l["level"] == "block" for l in meta.get("lints", []))
            n_warn = sum(l["level"] == "warn" for l in meta.get("lints", []))
            print(f"[{meta.get('status')}] {d.name}  —  {post.title}  (막힘 {n_block} / 경고 {n_warn})")


def _split(s: str | None) -> list[str]:
    return [k.strip() for k in (s or "").split(",") if k.strip()]


def _print_lints(lints: list[dict]) -> None:
    blocks = [l for l in lints if l["level"] == "block"]
    warns = [l for l in lints if l["level"] == "warn"]
    if blocks:
        print("\n발행 전에 고쳐야 할 항목:")
        for l in blocks:
            print(f"  ✗ {l['msg']}")
    if warns:
        print("\n확인 권장:")
        for l in warns:
            print(f"  ! {l['msg']}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="blogbot", description="네이버 블로그 글쓰기 도우미")
    ap.add_argument("--config", default="config.yaml")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("learn", help="예전 원고로 문체 학습 (+ 사진 분석)")
    s.add_argument("--skip-photos", action="store_true")
    s.set_defaults(func=cmd_learn)

    s = sub.add_parser("photos", help="새로 추가한 사진 분석")
    s.set_defaults(func=cmd_photos)

    def add_write_args(s):
        s.add_argument("--topic", required=True, help="글 주제")
        s.add_argument("--keyword", required=True, help="주 검색 키워드 1개")
        s.add_argument("--sub-keywords", help="보조 키워드, 쉼표로 구분")
        s.add_argument("--notes", help="이번 글만의 새 정보·직접 겪은 일 (필수)")
        s.add_argument("--notes-file", help="메모를 파일로 줄 때")
        s.add_argument("--photos", help="이번 글을 위해 새로 찍은 사진 폴더 (우선 사용)")
        s.add_argument("--kind", choices=["experience", "info", "promo"], default="experience")
        s.add_argument("--allow-empty-notes", action="store_true")

    def add_publish_args(s):
        s.add_argument("--mode", choices=["review", "draft", "publish"], help="config 의 naver.mode 대신 사용")
        s.add_argument("--ignore-overlap", action="store_true", help="유사도 경고를 확인했고 그대로 진행")
        s.add_argument("--republish", action="store_true", help="이미 올린 것으로 기록된 초안을 다시 올림")
        s.add_argument("--debug", action="store_true", help="단계별 스크린샷을 초안 폴더/debug 에 저장")

    s = sub.add_parser("write", help="새 글 초안 작성 + 검사 + 사진 가공")
    add_write_args(s)
    s.set_defaults(func=cmd_write)

    s = sub.add_parser("lint", help="직접 고친 초안을 다시 검사")
    s.add_argument("draft")
    s.set_defaults(func=cmd_lint)

    s = sub.add_parser("publish", help="초안을 네이버 에디터에 채우기")
    s.add_argument("draft", help="output/drafts/ 아래 초안 폴더")
    add_publish_args(s)
    s.set_defaults(func=cmd_publish)

    s = sub.add_parser("run", help="write → 확인 → publish (review/draft 모드만)")
    add_write_args(s)
    add_publish_args(s)
    s.set_defaults(func=cmd_run)

    s = sub.add_parser("check", help="발행한 글이 검색에 나오는지 확인·기록")
    s.add_argument("--resume", action="store_true", help="발행 중단 해제")
    s.set_defaults(func=cmd_check)

    s = sub.add_parser("login", help="네이버 로그인 (최초 1회, 직접 로그인)")
    s.set_defaults(func=cmd_login)

    s = sub.add_parser("drafts", help="초안 목록")
    s.set_defaults(func=cmd_drafts)

    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    try:
        args.func(args, cfg)
    except KeyboardInterrupt:
        sys.exit(130)
