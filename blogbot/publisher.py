"""Playwright 로 네이버 블로그 SmartEditor 에 글을 채우고 저장/발행한다.

네이버는 블로그 글쓰기 공개 API 를 제공하지 않으므로 실제 브라우저 화면을 조작한다.
- 로그인은 `login` 명령에서 사람이 직접 한다 (아이디/비밀번호를 저장하지 않음).
- 에디터 화면 구조가 바뀌면 config.yaml 의 naver.selectors 를 고친다.
- 기본 모드(review)는 내용을 채워두고 사람이 확인 후 직접 발행하도록 멈춘다.
"""

from __future__ import annotations

from pathlib import Path

from blogbot.writer import Post

WRITE_URL = "https://blog.naver.com/{blog_id}/postwrite"
LOGIN_URL = "https://nid.naver.com/nidlogin.login"


def _playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:  # pragma: no cover
        raise SystemExit("playwright 가 필요합니다: pip install playwright && playwright install chromium") from e
    return sync_playwright


def login(auth_path: Path) -> None:
    auth_path.parent.mkdir(parents=True, exist_ok=True)
    with _playwright()() as p:
        browser = p.chromium.launch(headless=False)
        ctx = browser.new_context(locale="ko-KR")
        page = ctx.new_page()
        page.goto(LOGIN_URL)
        input("열린 브라우저에서 네이버에 로그인한 뒤 여기서 Enter 를 누르세요... ")
        ctx.storage_state(path=str(auth_path))
        browser.close()
    print(f"로그인 세션 저장: {auth_path}")


def _click_if_visible(scope, selector: str, timeout: int = 2500) -> None:
    loc = scope.locator(selector).first
    try:
        loc.wait_for(state="visible", timeout=timeout)
        loc.click()
    except Exception:
        pass


def _type_lines(page, text: str) -> None:
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line:
            page.keyboard.insert_text(line)
        if i < len(lines) - 1:
            page.keyboard.press("Enter")


def fill_editor(post: Post, draft_dir: Path, images: dict[str, str], cfg: dict, auth_path: Path) -> str:
    """에디터에 글을 채우고 mode 에 따라 멈춤/임시저장/발행. 최종 mode 결과를 반환."""
    ncfg = cfg.get("naver", {})
    sel = ncfg.get("selectors", {})
    mode = ncfg.get("mode", "review")
    if not auth_path.exists():
        raise SystemExit("로그인 세션이 없습니다. 먼저 `python -m blogbot login` 을 실행하세요.")

    with _playwright()() as p:
        browser = p.chromium.launch(headless=bool(ncfg.get("headless", False)))
        ctx = browser.new_context(storage_state=str(auth_path), locale="ko-KR", viewport={"width": 1400, "height": 1000})
        page = ctx.new_page()
        page.goto(WRITE_URL.format(blog_id=ncfg["blog_id"]))
        page.wait_for_load_state("networkidle")

        frame_sel = sel.get("main_frame", "iframe#mainFrame")
        ed = page.frame_locator(frame_sel) if page.locator(frame_sel).count() else page

        ed.locator(sel["title"]).first.wait_for(timeout=20000)
        # "작성 중인 글이 있습니다" 팝업, 도움말 패널 닫기
        _click_if_visible(ed, sel.get("popup_cancel", "button.se-popup-button-cancel"))
        _click_if_visible(ed, sel.get("help_close", "button.se-help-panel-close-button"))

        # 제목
        ed.locator(sel["title"]).first.click()
        page.keyboard.insert_text(post.title)

        # 본문
        ed.locator(sel["body"]).first.click()
        for block in post.blocks:
            if block.kind in ("heading", "text"):
                _type_lines(page, block.text)
                page.keyboard.press("Enter")
                if block.kind == "heading":
                    page.keyboard.press("Enter")
            elif block.kind == "image":
                file = draft_dir / "images" / images[block.photo]
                count = ed.locator(".se-component.se-image").count()
                with page.expect_file_chooser() as fc:
                    ed.locator(sel["image_button"]).first.click()
                fc.value.set_files(str(file))
                ed.locator(".se-component.se-image").nth(count).wait_for(timeout=60000)
                page.wait_for_timeout(800)
                # 업로드 후 이미지 아래 새 문단으로 커서 이동
                ed.locator(".se-component").last.click()
                page.keyboard.press("End")
                page.keyboard.press("Enter")
                if block.text:
                    _type_lines(page, block.text)
                    page.keyboard.press("Enter")

        result = mode
        if mode == "draft":
            ed.locator(sel["save_button"]).first.click()
            page.wait_for_timeout(2000)
        elif mode == "publish":
            ed.locator(sel["publish_button"]).first.click()
            tag_input = ed.locator(sel["tag_input"]).first
            tag_input.wait_for(timeout=10000)
            for tag in post.tags[:10]:
                tag_input.click()
                page.keyboard.insert_text(tag)
                page.keyboard.press("Enter")
            ed.locator(sel["confirm_publish_button"]).first.click()
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(3000)
        else:
            print("\n에디터에 내용을 채웠습니다. 브라우저에서 확인·수정 후 직접 [발행]을 눌러주세요.")
            print("태그: " + " ".join(post.tags))
            answer = input("발행을 마쳤으면 y, 발행하지 않았으면 n 을 입력하세요: ").strip().lower()
            result = "published" if answer == "y" else "not_published"

        browser.close()
        return "published" if result == "publish" else result
