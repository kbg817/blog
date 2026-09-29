# blogbot — 네이버 블로그 자동화

내가 예전에 쓴 원고로 **말투·구성·흐름을 학습**해서 새 글을 쓰고, 예전 사진을 **보정해 붙인 뒤**
네이버 블로그 에디터에 **자동으로 올려주는** 프로그램입니다.

```
data/manuscripts/ (예전 원고)  ─┐
                               ├─ learn ─▶ 문체 프로필 + 사진 설명 인덱스
data/photos/      (예전 사진)  ─┘
                                         │
주제·키워드 ─────────────────────────── write ─▶ output/drafts/<초안>/
                                         │         ├ post.json   (제목·본문·사진 배치·태그)
                                         │         ├ preview.md  (검토용 미리보기 + 경고)
                                         │         └ images/     (보정된 사진)
                                         ▼
                                      publish ─▶ 네이버 SmartEditor 에 입력 → 검토/임시저장/발행
```

## 설치

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

cp config.example.yaml config.yaml      # 블로그 ID, 사업 정보 등 수정
cp .env.example .env                    # 또는 export ANTHROPIC_API_KEY=...
```

## 사용법

```bash
# 0) 자료 넣기
#    data/manuscripts/ 에 예전 원고 (.txt / .md / .docx, 하위 폴더 OK)
#    data/photos/      에 예전 사진 (.jpg / .png / .webp)

# 1) 문체 학습 + 사진 분석 (원고가 바뀌면 다시 실행, 사진은 새 사진만 분석)
python -m blogbot learn

# 2) 네이버 로그인 (최초 1회 — 열린 브라우저에서 직접 로그인)
python -m blogbot login

# 3) 초안 작성
python -m blogbot write --topic "가을 신메뉴 소개" --keywords "강남 카페,가을 신메뉴" \
    --notes "밤라떼 출시, 10월 한정"

# 4) output/drafts/<초안>/preview.md 확인 → 필요하면 post.json 수정

# 5) 업로드
python -m blogbot publish output/drafts/<초안폴더>            # config 의 mode 사용
python -m blogbot publish output/drafts/<초안폴더> --mode publish

# 3+5 한 번에
python -m blogbot run --topic "..." --keywords "..."

python -m blogbot drafts     # 초안 목록과 상태
```

### 업로드 모드 (`naver.mode`)

| 모드 | 동작 |
| --- | --- |
| `review` (기본, 권장) | 제목·본문·사진을 에디터에 채워두고 멈춤. 직접 확인 후 [발행] |
| `draft` | 채운 뒤 네이버 [임시저장]까지 |
| `publish` | 태그 입력 후 [발행]까지 자동 |

## 어떻게 동작하나

**문체 학습 (`learn`)** — 모든 원고를 Claude 에 넣어 말투, 끝맺음, 자주 쓰는 표현, 이모티콘 습관,
문단 길이, 글 흐름(도입→…→마무리), 제품 언급 방식, 제목 패턴을 `state/style_profile.json` 으로 정리합니다.
사진은 한 장씩 보고 “무슨 사진인지”를 `state/photo_index.json` 에 저장합니다.

**글쓰기 (`write`)** — 문체 프로필 + 주제와 가장 비슷한 과거 원고 3편(예시) + `config.yaml` 의 사업 정보로
새 글을 씁니다. 사진은 설명을 보고 문단에 맞는 것을 골라 배치합니다.
- `business.facts` 에 없는 가격·효능·후기는 지어내지 않고 `[확인필요: …]` 로 남깁니다.
  이 표시가 남아 있으면 `publish` 가 멈춥니다.
- 초안을 과거 원고와 비교해 **문장이 겹치는 비율**을 계산하고, 거의 그대로 옮긴 문장을
  `preview.md` 에 표시합니다. 기준(`max_overlap_ratio`)을 넘으면 `publish` 가 멈춥니다.

**사진 보정 (`images`)** — 가장자리 크롭, 본문 폭(900px) 리사이즈, 밝기·대비·채도 미세 보정, 샤픈,
워터마크/로고(선택), 그리고 촬영 기기·GPS 등 **메타데이터 제거**. 글마다 보정값이 달라집니다.

**재사용·발행 제한 (`state/ledger.json`)** — 한 사진은 `reuse_cooldown_days` 안에 다시 쓰지 않고,
최대 `max_uses_per_photo` 번까지만 씁니다. 하루 발행은 `max_posts_per_day` 건까지입니다.

## 꼭 알아두실 점

- **사진 보정이 저품질을 막아주지는 않습니다.** 네이버가 이미지를 어떻게 판별하는지는 공개되어 있지 않고,
  크롭·색보정 정도로 같은 사진인지 못 알아본다고 기대하긴 어렵습니다. 그래서 이 프로그램은 보정을
  “숨기기”가 아니라 글에 맞게 다듬는 용도로 쓰고, 실제 대책은 **같은 사진을 자주 쓰지 않는 것**(재사용 제한)과
  **새 사진을 꾸준히 추가하는 것**에 둡니다. `photos` 명령은 새 사진만 분석하니 수시로 넣어주세요.
- **저품질의 가장 큰 원인은 비슷한 글을 기계적으로 많이 올리는 것**입니다. 하루 1건 이하, 사람이 확인하는
  `review` 모드, 그리고 매번 `--notes` 로 이번 글만의 새 정보(신메뉴, 실제 후기, 이벤트 등)를 넣는 것을 권장합니다.
  노출은 결국 검색하는 사람에게 도움이 되는 새 정보가 있는 글에서 나옵니다.
- 네이버는 블로그 글쓰기 공개 API 를 제공하지 않아 브라우저 화면을 자동 조작합니다. 자동화 도구 사용은
  네이버 이용약관상 제재 대상이 될 수 있으니 계정 운영은 본인 판단으로 해주세요.
  로그인은 직접 하고(비밀번호 저장 안 함), 캡차 우회 같은 기능은 넣지 않았습니다.
- SmartEditor 화면 구조는 자주 바뀝니다. **처음에는 `review` 모드로 돌려서** 제목/본문/사진이 제자리에
  들어가는지 확인하고, 어긋나면 `config.yaml` 의 `naver.selectors` 를 고쳐주세요.
- 대가를 받은 후기·협찬 글이라면 공정위 지침에 따라 광고 표기를 해야 합니다.

## 개발

```bash
pip install pytest
python -m pytest -q          # API 호출 없이 동작 (Claude 응답은 가짜로 대체)
```

구성: `blogbot/corpus.py`(원고·유사도) · `style.py`(문체 프로필) · `photos.py`(사진 인덱스·사용 기록) ·
`images.py`(사진 보정) · `writer.py`(글 생성·초안) · `publisher.py`(네이버 업로드) · `cli.py`(명령어)
