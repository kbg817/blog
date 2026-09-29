"""Playwright 로 네이버 블로그 SmartEditor 에 글을 채운다.

네이버는 블로그 글쓰기 공개 API 를 2020-05-06 에 종료했다. 그래서 실제 브라우저 화면을 조작한다.
네이버 이용약관은 '자동화된 수단으로 게시물을 게재하는 행위'를 금지하므로, 이 모듈은
계정 위험을 최소로 하는 쪽으로만 동작한다:

- 로그인은 사람이 직접 한다. 비밀번호를 저장하거나 입력하지 않는다.
- 항상 같은 브라우저 프로필(.auth/browser-profile)을 쓴다. 매번 새 환경으로 접속하지 않는다.
- 화면이 보이는 브라우저만 쓴다 (headless 금지). 탐지 회피 옵션은 쓰지 않는다.
- 기본(review)은 글·사진·태그·발행 설정까지 채우고, 최종 [발행]/[예약]은 사람이 누른다.
- 보호조치·이용제한·로그인 만료를 만나면 즉시 멈춘다. 자동 재시도·자동 재로그인은 하지 않는다.
- 글쓰기 화면 외에 다른 페이지를 자동으로 돌아다니지 않는다 (공감·댓글·이웃 자동화 없음).

SmartEditor 화면 구조는 자주 바뀐다. 셀렉터는 config.yaml 의 naver.selectors 에서
문자열 또는 목록(앞에서부터 시도)으로 바꿀 수 있다.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from blogbot.writer import Post

WRITE_URL = "https://blog.naver.com/{blog_id}/postwrite"
LOGIN_URL = "https://nid.naver.com/nidlogin.login"
POST_URL_RE = re.compile(r"blog\.naver\.com/(?:[^/?#]+/(\d{6,})|.*[?&]logNo=(\d+))")

DEFAULT_SELECTORS = {
    "main_frame": "iframe#mainFrame",
    "popup_cancel": ["button.se-popup-button-cancel", "button.se-popup-button-close"],
    "help_close": "button.se-help-panel-close-button",
    "title": [".se-documentTitle .se-text-paragraph", "[data-a11y-title='제목'] .se-text-paragraph"],
    "body": [".se-component.se-text .se-text-paragraph", "[data-a11y-title='본문'] .se-text-paragraph"],
    "image_button": ["button[data-name='image']", "button.se-image-toolbar-button"],
    "text_format_button": ["button.se-text-format-toolbar-button"],
    "section_title_option": ["button.se-toolbar-option-text-format-sectionTitle-button"],
    "body_text_option": ["button.se-toolbar-option-text-format-text-button"],
    "quote_button": ["button[data-name='quotation']", "button.se-insert-quotation-default-toolbar-button"],
    "divider_button": ["button[data-name='horizontal-line']", "button.se-insert-horizontal-line-default-toolbar-button"],
    "save_button": ["button[data-click-area='tpb.save']"],
    "publish_button": ["button[data-click-area='tpb.publish']"],
    "publish_layer": ["div[class*='layer_publish']", "div[class*='publish_layer']"],
    "category_button": ["button[data-click-area='tpb*i.category']", "button[class*='selectbox_button']"],
    "category_item": ["label:text-is(\"{name}\")", "span:text-is(\"{name}\")", "label:has-text(\"{name}\")"],
    "open_public": ["#open_public", "input[value='0'][name*='open']"],
    "search_allow": ["#publish-option-search"],
    "tag_input": ["input#tag-input", "input[placeholder*='태그']"],
    "confirm_publish_button": ["button[data-testid='seOnePublishBtn']", "button[data-click-area='tpb*i.publish']"],
    "captcha_frame": "iframe[id^='ncaptcha-iframe']",
}


class Blocked(SystemExit):
    """계정 보호조치·이용제한·로그인 만료. 자동으로 풀려고 하지 않고 멈춘다."""


@dataclass
class Result:
    status: str  # published / scheduled / not_published / saved_to_naver_draft / unknown
    url: str = ""
    log_no: str = ""
    notes: list[str] = field(default_factory=list)


def _playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:  # pragma: no cover
        raise SystemExit("playwright 가 필요합니다: pip install playwright && playwright install chromium") from e
    return sync_playwright


def _launch(p, cfg: dict, profile_dir: Path):
    ncfg = cfg.get("naver", {})
    profile_dir.mkdir(parents=True, exist_ok=True)
    kwargs = dict(
        user_data_dir=str(profile_dir),
        headless=False,
        locale="ko-KR",
        viewport={"width": 1400, "height": 1000},
    )
    if ncfg.get("browser_channel"):  # 예: "chrome" — 설치된 크롬 사용
        kwargs["channel"] = ncfg["browser_channel"]
    if ncfg.get("browser_executable"):
        kwargs["executable_path"] = ncfg["browser_executable"]
    return p.chromium.launch_persistent_context(**kwargs)


def login(cfg: dict, profile_dir: Path) -> None:
    with _playwright()() as p:
        ctx = _launch(p, cfg, profile_dir)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(LOGIN_URL)
        print("열린 브라우저에서 네이버에 직접 로그인하세요. '로그인 상태 유지'를 켜 두면 세션이 오래 갑니다.")
        input("로그인을 마쳤으면 여기서 Enter 를 누르세요... ")
        ok = any(c["name"] == "NID_AUT" for c in ctx.cookies())
        ctx.close()
    if not ok:
        raise SystemExit("로그인 쿠키를 찾지 못했습니다. 다시 `python -m blogbot login` 을 실행해 주세요.")
    print(f"로그인 세션이 브라우저 프로필에 저장됐습니다: {profile_dir}")


# ---- 셀렉터 도우미 ---------------------------------------------------------


class Editor:
    def __init__(self, page, cfg: dict, debug_dir: Path | None):
        self.page = page
        self.sel = {**DEFAULT_SELECTORS, **(cfg.get("naver", {}).get("selectors") or {})}
        self.scope = page
        self.debug_dir = debug_dir
        self.shot = 0

    def candidates(self, key: str, **fmt) -> list[str]:
        v = self.sel.get(key) or []
        items = [v] if isinstance(v, str) else list(v)
        return [s.format(**fmt) if fmt else s for s in items]

    def find(self, key: str, timeout: float = 5.0, scope=None, **fmt):
        """셀렉터 후보 중 처음으로 보이는 요소. 없으면 None."""
        scope = scope or self.scope
        deadline = time.time() + timeout
        while True:
            for s in self.candidates(key, **fmt):
                loc = scope.locator(s).first
                try:
                    if loc.count() and loc.is_visible():
                        return loc
                except Exception:
                    pass
            if time.time() >= deadline:
                return None
            self.page.wait_for_timeout(250)

    def need(self, key: str, timeout: float = 10.0, **fmt):
        loc = self.find(key, timeout, **fmt)
        if loc is None:
            self.screenshot(f"missing-{key}")
            raise RuntimeError(
                f"에디터에서 '{key}' 요소를 찾지 못했습니다. config.yaml 의 naver.selectors.{key} 를 확인해 주세요."
            )
        return loc

    def screenshot(self, name: str) -> None:
        if self.debug_dir:
            self.debug_dir.mkdir(parents=True, exist_ok=True)
            self.shot += 1
            try:
                self.page.screenshot(path=str(self.debug_dir / f"{self.shot:02d}-{name}.png"))
            except Exception:
                pass


def _check_account_state(page, ed: Editor) -> None:
    url = page.url
    parsed = urlparse(url)
    if parsed.netloc.endswith("nid.naver.com"):
        if "idSafety" in parsed.path or parsed.path.startswith("/user2/help"):
            raise Blocked(
                "네이버 아이디 보호조치 화면입니다. 브라우저에서 직접 본인 확인으로 해제한 뒤, "
                "하루 정도 지나서 다시 시도하세요. (자동 재시도하지 않습니다)"
            )
        raise Blocked("로그인이 만료됐습니다. `python -m blogbot login` 으로 직접 다시 로그인해 주세요.")
    body = ""
    for frame in page.frames:  # 에디터가 iframe 안에 있으면 안내도 그 안에 뜰 수 있다
        try:
            body += frame.locator("body").inner_text(timeout=1500) + "\n"
        except Exception:
            pass
    if re.search(r"이용이\s*제한|게시\s*제한|이용\s*제한\s*(중|되었|된|안내)", body):
        raise Blocked("계정 또는 게시 이용제한 안내가 보입니다. 네이버 고객센터에서 사유를 확인하세요. 발행을 멈춥니다.")


def _wait_for_human_if_captcha(page, ed: Editor) -> None:
    for frame_loc in page.locator(ed.candidates("captcha_frame")[0]).all():
        try:
            box = frame_loc.bounding_box()
        except Exception:
            box = None
        if box and box["width"] > 10 and box["height"] > 10:
            input("추가 확인(캡차)이 떴습니다. 브라우저에서 직접 풀고 Enter 를 누르세요... ")
            return


def _open_editor(page, ed: Editor, blog_id: str) -> None:
    page.goto(WRITE_URL.format(blog_id=blog_id), wait_until="domcontentloaded")
    _check_account_state(page, ed)
    # 에디터가 최상위 문서 또는 #mainFrame iframe 안에 늦게 붙을 수 있으므로 둘 다 폴링
    frame = page.frame_locator(ed.candidates("main_frame")[0])
    deadline = time.time() + 30
    while time.time() < deadline:
        for scope in (page, frame):
            if ed.find("title", timeout=0, scope=scope) is not None:
                ed.scope = scope
                return
        _check_account_state(page, ed)
        page.wait_for_timeout(500)
    ed.screenshot("editor-timeout")
    raise RuntimeError("글쓰기 화면이 30초 안에 열리지 않았습니다.")


def _focus_after_last(page, ed: Editor) -> None:
    """방금 넣은 컴포넌트(사진·인용구·구분선) 아래의 빈 문단으로 커서를 옮긴다."""
    comps = ed.scope.locator(".se-component")
    last = comps.last
    cls = last.get_attribute("class") or ""
    if "se-text" in cls:
        last.locator(".se-text-paragraph").last.click()
        page.keyboard.press("End")
        return
    # 에디터가 빈 문단을 만들지 않은 경우: 컴포넌트를 선택한 뒤 아래로 이동해 새 문단을 만든다
    last.click()
    page.keyboard.press("ArrowDown")
    page.keyboard.press("End")


def _type_lines(page, text: str) -> None:
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line:
            page.keyboard.insert_text(line)
        if i < len(lines) - 1:
            page.keyboard.press("Enter")


def _apply_section_title(page, ed: Editor) -> bool:
    btn = ed.find("text_format_button", timeout=1.5)
    if btn is None:
        return False
    btn.click()
    opt = ed.find("section_title_option", timeout=1.5)
    if opt is None:
        page.keyboard.press("Escape")
        return False
    opt.click()
    return True


def _upload_image(page, ed: Editor, file: Path) -> None:
    images = ed.scope.locator(".se-component.se-image")
    before = images.count()
    with page.expect_file_chooser(timeout=15000) as fc:
        ed.need("image_button").click()
    fc.value.set_files(str(file))
    images.nth(before).wait_for(timeout=60000)
    # 업로드가 끝나면 blob: 주소가 네이버 이미지 주소로 바뀐다. 그 전에 발행하면 사진이 빠진다.
    deadline = time.time() + 60
    while time.time() < deadline:
        if images.nth(before).locator("img[src^='blob:']").count() == 0:
            break
        page.wait_for_timeout(500)
    else:
        raise RuntimeError(f"사진 업로드가 끝나지 않았습니다: {file.name}")


def _fill(page, ed: Editor, post: Post, draft_dir: Path, images: dict, notes: list[str]) -> None:
    _check_account_state(page, ed)
    _wait_for_human_if_captcha(page, ed)
    for key in ("popup_cancel", "help_close"):
        loc = ed.find(key, timeout=2)
        if loc is not None:
            loc.click()

    ed.need("title").click()
    page.keyboard.insert_text(post.title)
    ed.need("body").click()

    heading_warned = False
    for i, block in enumerate(post.blocks):
        if block.kind == "heading":
            styled = _apply_section_title(page, ed)
            if not styled and not heading_warned:
                notes.append("소제목 서식 버튼을 찾지 못해 소제목을 일반 문단으로 넣었습니다 (직접 소제목으로 바꿔 주세요).")
                heading_warned = True
            page.keyboard.insert_text(block.text)
            page.keyboard.press("Enter")
            if styled:  # 다음 문단은 본문 서식으로
                _apply_body_style(page, ed)
        elif block.kind == "text":
            _type_lines(page, block.text)
            page.keyboard.press("Enter")
        elif block.kind == "quote":
            btn = ed.find("quote_button", timeout=1.5)
            if btn is None:
                _type_lines(page, block.text)
                page.keyboard.press("Enter")
                notes.append("인용구 버튼을 찾지 못해 인용구를 일반 문단으로 넣었습니다.")
            else:
                btn.click()
                page.wait_for_timeout(400)
                _type_lines(page, block.text)
                _focus_after_last(page, ed)
                page.keyboard.press("Enter")
        elif block.kind == "divider":
            btn = ed.find("divider_button", timeout=1.5)
            if btn is not None:
                btn.click()
                page.wait_for_timeout(400)
                _focus_after_last(page, ed)
        elif block.kind == "image":
            info = images.get(block.photo)
            if not info:
                continue
            _upload_image(page, ed, draft_dir / "images" / info["file"])
            _focus_after_last(page, ed)
            if block.text:
                _type_lines(page, block.text)
                page.keyboard.press("Enter")
        if i % 5 == 4:
            ed.screenshot(f"block-{i}")
    ed.screenshot("filled")


def _apply_body_style(page, ed: Editor) -> None:
    """소제목 다음 문단이 소제목 서식을 이어받았으면 본문으로 되돌린다 (가능한 경우에만)."""
    btn = ed.find("text_format_button", timeout=1)
    if btn is None:
        return
    btn.click()
    body_opt = ed.find("body_text_option", timeout=1)
    if body_opt is not None:
        body_opt.click()
    else:
        page.keyboard.press("Escape")


def _open_publish_layer(page, ed: Editor, post: Post, cfg: dict, notes: list[str]) -> None:
    ncfg = cfg.get("naver", {})
    ed.need("publish_button").click()
    ed.find("publish_layer", timeout=5)

    layer = ed.find("publish_layer", timeout=1)
    category = (ncfg.get("category") or "").strip()
    if category:
        btn = ed.find("category_button", timeout=3)
        item = None
        if btn is not None:
            btn.click()
            # 본문 속 같은 단어를 누르지 않도록 발행 레이어 안에서만 찾는다
            item = ed.find("category_item", timeout=3, scope=layer, name=category.replace('"', '\\"'))
        if item is not None:
            item.click()
        else:
            notes.append(f"카테고리 '{category}' 를 자동으로 고르지 못했습니다. 직접 선택해 주세요.")

    tag_input = ed.find("tag_input", timeout=5)
    if tag_input is None:
        notes.append("태그 입력칸을 찾지 못했습니다. 태그를 직접 넣어 주세요: " + " ".join(post.tags))
    else:
        for tag in post.tags[:10]:
            tag_input.click()
            page.keyboard.insert_text(tag)
            page.keyboard.press("Enter")
            page.wait_for_timeout(150)

    # 전체공개 + 검색허용이 아니면 글이 검색에 아예 안 나온다 (직전 글 설정이 남아 있을 수 있음)
    for key, label in (("open_public", "전체공개"), ("search_allow", "검색허용")):
        box = ed.find(key, timeout=2)
        try:
            if box is not None and not box.is_checked():
                box.check()
        except Exception:
            box = None
        if box is None or not box.is_checked():
            notes.append(f"'{label}' 설정을 확인하지 못했습니다. 발행 전에 직접 확인해 주세요.")
    ed.screenshot("publish-layer")


def _post_url(page) -> tuple[str, str]:
    """발행된 글 주소 (에디터가 iframe 안에 있으면 iframe 이 이동할 수 있어 모든 프레임을 본다)."""
    for url in [page.url] + [f.url for f in page.frames]:
        m = POST_URL_RE.search(url)
        if m and "postwrite" not in url and "PostWriteForm" not in url:
            return url, m.group(1) or m.group(2)
    return "", ""


def _wait_for_post_url(page, seconds: float) -> tuple[str, str]:
    deadline = time.time() + seconds
    while time.time() < deadline:
        found = _post_url(page)
        if found[1]:
            return found
        page.wait_for_timeout(500)
    return "", ""


def fill_editor(
    post: Post, draft_dir: Path, images: dict, cfg: dict, profile_dir: Path, debug: bool = False
) -> Result:
    ncfg = cfg.get("naver", {})
    mode = ncfg.get("mode", "review")
    notes: list[str] = []
    phase = "start"
    with _playwright()() as p:
        ctx = _launch(p, cfg, profile_dir)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        ed = Editor(page, cfg, draft_dir / "debug" if debug else None)
        try:
            _open_editor(page, ed, ncfg["blog_id"])
            phase = "filling"
            _fill(page, ed, post, draft_dir, images, notes)
            phase = "filled"

            if mode == "draft":
                ed.need("save_button").click()
                page.wait_for_timeout(2500)
                return Result("saved_to_naver_draft", notes=notes)

            _open_publish_layer(page, ed, post, cfg, notes)
            phase = "layer_open"

            if mode == "publish":
                if notes:  # 자동 발행은 모든 단계가 확인됐을 때만
                    raise RuntimeError("자동 입력 중 확인되지 않은 부분이 있어 자동 발행을 멈췄습니다: " + " / ".join(notes))
                phase = "confirm_clicked"
                ed.need("confirm_publish_button").click()
                url, log_no = _wait_for_post_url(page, 30)
                return Result("published" if log_no else "unknown", url or page.url, log_no, notes)

            # review: 사람이 확인하고 최종 버튼을 누른다
            for n in notes:
                print("  ⚠", n)
            print("\n글·사진·태그·발행 설정을 채웠습니다. 브라우저에서 확인·수정한 뒤")
            print("발행 레이어의 [발행] 버튼(또는 예약 발행)을 직접 눌러 주세요.")
            input("다 끝났으면 여기서 Enter 를 누르세요... ")
            url, log_no = _post_url(page)
            # 예약 후 블로그 홈으로 가면 예전 글 주소가 보일 수 있으므로 사람이 확정한다
            hint = f"(감지된 글 주소: {url}) " if log_no else "(발행된 글 주소를 찾지 못했습니다) "
            ans = input(hint + "[p]발행함 / [r]예약함 / [n]발행 안 함: ").strip().lower()
            status = {"p": "published", "r": "scheduled", "n": "not_published"}.get(ans, "unknown")
            if status == "published" and log_no:
                return Result(status, url, log_no, notes)
            return Result(status, page.url, "", notes)
        except Blocked:
            raise
        except Exception as e:
            ed.screenshot("error")
            if phase == "confirm_clicked":
                notes.append(f"발행 버튼을 누른 뒤 오류: {e}")
                return Result("unknown", page.url, "", notes)
            print(f"\n오류: {e}")
            if phase in ("filling", "filled", "layer_open"):
                ans = input("브라우저에서 직접 마무리했나요? [p]발행함 / [r]예약함 / [n]아니요: ").strip().lower()
                status = {"p": "published", "r": "scheduled"}.get(ans)
                if status:
                    notes.append(f"자동 입력 중 오류 후 직접 마무리: {e}")
                    return Result(status, page.url, "", notes)
            raise
        finally:
            try:
                ctx.close()
            except Exception:
                pass
