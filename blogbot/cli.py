"""명령행 진입점: python -m blogbot <명령>"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

from blogbot.config import load_config, paths


def _seed(*parts: str) -> int:
    return int(hashlib.sha1("|".join(parts).encode()).hexdigest()[:8], 16)


def cmd_learn(args, cfg) -> None:
    from blogbot.corpus import load_manuscripts
    from blogbot.style import build_profile, save_profile

    P = paths()
    docs = load_manuscripts(P.manuscripts)
    print(f"원고 {len(docs)}편으로 문체를 분석합니다...")
    w = cfg.get("writing", {})
    profile = build_profile(docs, w.get("model", "claude-opus-5-5"), w.get("effort", "high"))
    save_profile(profile, P.style_profile)
    print(f"문체 프로필 저장: {P.style_profile}")
    if not args.skip_photos:
        cmd_photos(args, cfg)


def cmd_photos(args, cfg) -> None:
    from blogbot.corpus import list_photos
    from blogbot.photos import index_photos

    P = paths()
    photos = list_photos(P.photos)
    print(f"사진 {len(photos)}장 인덱싱 (새 사진만 분석)...")
    index_photos(photos, P.photos, P.photo_index, cfg.get("writing", {}).get("model", "claude-opus-5-5"))
    print(f"사진 인덱스 저장: {P.photo_index}")


def cmd_write(args, cfg) -> Path:
    from blogbot.corpus import load_manuscripts
    from blogbot.images import process_image
    from blogbot.photos import Ledger, load_json
    from blogbot.style import load_profile
    from blogbot.writer import new_draft_dir, save_draft, write_post

    P = paths()
    icfg = cfg.get("images", {})
    docs = load_manuscripts(P.manuscripts)
    profile = load_profile(P.style_profile)
    ledger = Ledger(P.ledger)
    index = load_json(P.photo_index, {})
    available = {
        k: v
        for k, v in index.items()
        if (P.photos / k).exists()
        and ledger.photo_available(k, int(icfg.get("reuse_cooldown_days", 30)), int(icfg.get("max_uses_per_photo", 3)))
    }
    if index and not available:
        print("경고: 재사용 제한에 걸리지 않은 사진이 없습니다. 새 사진을 추가하는 것을 권장합니다.")

    keywords = [k.strip() for k in (args.keywords or "").split(",") if k.strip()]
    print(f"'{args.topic}' 글을 작성합니다...")
    post = write_post(
        topic=args.topic,
        keywords=keywords,
        notes=args.notes or "",
        profile=profile,
        docs=docs,
        photo_candidates=available,
        cfg=cfg,
    )

    d = new_draft_dir(post, P.drafts)
    images: dict[str, str] = {}
    for i, b in enumerate([b for b in post.blocks if b.kind == "image"], start=1):
        name = f"{i:02d}.jpg"
        process_image(P.photos / b.photo, d / "images" / name, icfg, _seed(d.name, b.photo))
        images[b.photo] = name

    save_draft(d, post, docs, float(cfg.get("writing", {}).get("max_overlap_ratio", 0.15)), images)
    print(f"초안 저장: {d}")
    print(f"  미리보기: {d / 'preview.md'}")
    return d


def cmd_publish(args, cfg, draft_dir: Path | None = None) -> None:
    from blogbot.photos import Ledger
    from blogbot.publisher import fill_editor
    from blogbot.writer import load_draft, update_meta

    P = paths()
    draft_dir = draft_dir or Path(args.draft)
    post, meta = load_draft(draft_dir)
    if args.mode:
        cfg.setdefault("naver", {})["mode"] = args.mode

    if meta.get("status") == "published" and not args.force:
        raise SystemExit("이미 발행한 초안입니다. 다시 올리려면 --force")
    if meta.get("overlap_warning") and not args.force:
        raise SystemExit(
            f"과거 원고와 겹치는 비율이 {meta['overlap_ratio']:.1%} 입니다. "
            "preview.md 를 보고 post.json 을 고치거나 --force 로 진행하세요."
        )
    if meta.get("needs_check") and not args.force:
        raise SystemExit("[확인필요] 표시가 남아 있습니다: " + ", ".join(meta["needs_check"]))

    ledger = Ledger(P.ledger)
    limit = int(cfg.get("schedule", {}).get("max_posts_per_day", 1))
    if ledger.posts_today() >= limit and not args.force:
        raise SystemExit(f"오늘 발행 한도({limit}건)에 도달했습니다.")

    result = fill_editor(post, draft_dir, meta.get("images", {}), cfg, P.auth)
    print(f"결과: {result}")
    if result == "published":
        ledger.record_photos([b.photo for b in post.blocks if b.kind == "image"])
        ledger.record_post(draft_dir.name, post.title, cfg["naver"].get("mode", "review"))
        ledger.save()
        update_meta(draft_dir, status="published")
    elif result == "draft":
        update_meta(draft_dir, status="saved_to_naver_draft")


def cmd_run(args, cfg) -> None:
    d = cmd_write(args, cfg)
    cmd_publish(args, cfg, draft_dir=d)


def cmd_login(args, cfg) -> None:
    from blogbot.publisher import login

    login(paths().auth)


def cmd_drafts(args, cfg) -> None:
    from blogbot.writer import load_draft

    root = paths().drafts
    if not root.exists():
        print("초안이 없습니다.")
        return
    for d in sorted(root.iterdir()):
        if (d / "post.json").exists():
            post, meta = load_draft(d)
            print(f"[{meta.get('status')}] {d}  —  {post.title}  (겹침 {meta.get('overlap_ratio', 0):.1%})")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="blogbot", description="네이버 블로그 자동화")
    ap.add_argument("--config", default="config.yaml")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("learn", help="과거 원고로 문체 프로필 만들기 (+ 사진 인덱싱)")
    s.add_argument("--skip-photos", action="store_true")
    s.set_defaults(func=cmd_learn)

    s = sub.add_parser("photos", help="새로 추가한 사진 분석")
    s.set_defaults(func=cmd_photos)

    def add_write_args(s):
        s.add_argument("--topic", required=True, help="글 주제")
        s.add_argument("--keywords", help="검색 키워드, 쉼표로 구분")
        s.add_argument("--notes", help="이번 글에 꼭 넣을 내용/메모")

    def add_publish_args(s):
        s.add_argument("--mode", choices=["review", "draft", "publish"], help="config 의 naver.mode 대신 사용")
        s.add_argument("--force", action="store_true", help="겹침/확인필요/일일 한도 검사 무시")

    s = sub.add_parser("write", help="새 글 초안 작성 + 사진 가공")
    add_write_args(s)
    s.set_defaults(func=cmd_write)

    s = sub.add_parser("publish", help="초안을 네이버 에디터에 올리기")
    s.add_argument("draft", help="output/drafts/ 아래 초안 폴더")
    add_publish_args(s)
    s.set_defaults(func=cmd_publish)

    s = sub.add_parser("run", help="write + publish 한 번에")
    add_write_args(s)
    add_publish_args(s)
    s.set_defaults(func=cmd_run)

    s = sub.add_parser("login", help="네이버 로그인 세션 저장 (최초 1회)")
    s.set_defaults(func=cmd_login)

    s = sub.add_parser("drafts", help="초안 목록")
    s.set_defaults(func=cmd_drafts)

    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    try:
        args.func(args, cfg)
    except KeyboardInterrupt:
        sys.exit(130)
