# 고려대 학식 카카오톡 챗봇

고려대학교 공식 식단 페이지를 주기적으로 수집하고, 카카오톡 채널
챗봇의 스킬 요청에 응답하는 무료 운영용 프로젝트입니다. 의과대학
본관식당은 OCR하지 않고 공식 주간식단표 원본 이미지를 전달합니다.

## 구조

```text
고려대 공식 식단 페이지
  → GitHub Actions: 주기적 수집
  → GitHub Pages: public/data/menu.json + 의대본관 식단 이미지
  → Cloudflare Worker: 카카오 텍스트/simpleImage 변환
  → 카카오톡 채널 챗봇
```

GitHub Pages는 정적 호스팅이라 카카오의 POST 요청을 직접 처리하지
못합니다. Worker는 HTML을 파싱하지 않고 이미 수집된 JSON만 읽으므로
무료 플랜의 짧은 CPU 제한 안에서 동작하도록 구성했습니다.

## 1. 로컬에서 식단 수집 확인

Python 3.11 이상이 필요하며 외부 Python 패키지는 사용하지 않습니다.

```bash
python3 scraper/fetch_menus.py
python3 scraper/fetch_medicine_menu.py
python3 -m http.server 8000 --directory public
```

의대본관 수집기는 의과대학 홈페이지의 `식당` 제목 검색 목록에서
`[의과대학본관식당] 주간식단표` 게시글만 선택합니다. 다음 주 게시글이
미리 등록되어 있어도 실행일이 포함된 주간표를 우선하며, 이미지는
`public/data/medicine-menu/article-게시글번호.png` 또는 `.jpg`로 저장합니다.
PNG/JPEG 형식과 이미지 크기를 검증하지 못하면 종료 코드 1로 배포를
중단하므로 직전 정상 Pages가 유지됩니다.

브라우저에서 `http://localhost:8000`을 엽니다.

테스트:

```bash
python3 -m unittest discover -s tests
```

## 2. GitHub Pages 배포

1. 이 폴더를 Git 저장소로 초기화하고 GitHub의 **공개 저장소**에 push합니다.
2. GitHub 저장소 `Settings → Pages`에서 Source를 **GitHub Actions**로 설정합니다.
3. `Actions → Collect menus and deploy Pages → Run workflow`를 한 번 실행합니다.
4. 아래 주소에서 JSON이 보이는지 확인합니다.

```text
https://GITHUB_USERNAME.github.io/REPOSITORY_NAME/data/menu.json
```

워크플로는 매일 한국시간 07:00에 다시 수집합니다.
직전 Pages JSON과 새 데이터를 병합해 최근 90일 식단을 유지합니다. 일부
식당 수집이 실패하면 해당 식당의 최근 정상 기록을 보존하고, 전부 실패하면
배포 단계로 넘어가지 않아 마지막 정상 배포가 유지됩니다. 누적 JSON은
240 KiB를 넘기기 전에 가장 오래된 날짜부터 제거해 Worker의 256 KiB 입력
제한 안에 머물도록 합니다.

## 3. Cloudflare Worker 로컬 테스트

Node.js 22 이상이 필요합니다. Wrangler는 잠금파일에 고정된 버전을
재현하도록 `npm install` 대신 `npm ci`로 설치합니다.

```bash
cd worker
npm ci
npm test
```

`worker/wrangler.jsonc`의 `MENU_DATA_URL`을 실제 Pages JSON 주소로 바꿉니다.

```bash
npm run dev
```

다른 터미널에서:

```bash
curl http://localhost:8787/kakao/meal \
  -X POST \
  -H "content-type: application/json" \
  -H "x-kakao-skill-secret: 로컬-.dev.vars와-동일한-32자-이상-문자열" \
  -d '{"userRequest":{"utterance":"자연계 오늘 점심"}}'
```

## 4. Worker 무료 배포

Cloudflare 대시보드에서 Workers Free 플랜인지 확인한 후:

```bash
cd worker
npx wrangler login
npx wrangler secret put KAKAO_SKILL_SECRET
npm run deploy
```

비밀값은 32자 이상이어야 하며, 설정하지 않았거나 너무 짧으면 Worker가
`503 Service Misconfigured`로 종료됩니다. 저장소나 명령행 인자에 비밀값을
직접 적지 말고 `wrangler secret put`의 프롬프트에서 입력합니다.

배포 후 다음과 같은 주소를 받습니다.

```text
https://ku-meal-kakao-skill.ACCOUNT_SUBDOMAIN.workers.dev
```

상태 확인:

```bash
curl https://ku-meal-kakao-skill.ACCOUNT_SUBDOMAIN.workers.dev/health \
  -H "x-kakao-skill-secret: 설정한_32자_이상_비밀값"
```

`.dev.vars`는 로컬 전용이며 Git에 올리지 않습니다.

```dotenv
KAKAO_SKILL_SECRET=로컬테스트용-32자-이상의-충분히-긴-임의-문자열
```

## 5. 카카오 챗봇 연결

챗봇 관리자센터에서:

1. `스킬 → 생성`
2. URL에 다음 주소 입력

   ```text
   https://ku-meal-kakao-skill.ACCOUNT_SUBDOMAIN.workers.dev/kakao/meal
   ```

3. 헤더에 Worker secret과 같은 값을 입력

   ```text
   x-kakao-skill-secret: Worker에_등록한_동일한_32자_이상_문자열
   ```

4. 스킬 테스트에서 `자연계 오늘 점심`을 전송
5. 일반 블록을 만들고 `오늘 학식`, `내일 학식`, `자연계`, `산학관`,
   `학생회관`, `안암학사`, `의대본관` 등의 발화를 등록
6. 블록 응답을 해당 스킬에 연결하고 개발 채널에서 테스트
7. 정상 작동하면 운영 채널에 연결하고 배포

## 지원 발화

- `밥`: 모든 식당의 오늘 중식 요약
- `오늘 학식`: 모든 식당의 오늘 중식 요약
- `어제 학식`, `그저께 자연계 점심`, `모레 안암학사 저녁`
- `지난주 금요일 긱식`, `다음주 월요일 산학관`
- `7월 29일 학관`, `7/29 자연대 점심`
- `자연계 오늘 점심`
- `산학관 저녁`
- `내일 안암학사`
- `의대본관 오늘 점심`, `의대식당`
- `2026-07-31 학생회관 점심`

식당을 지정하면 상세 메뉴를, 지정하지 않으면 식당별 메뉴를 요약합니다.
의대본관을 지정하면 주간 범위와 공식 원문 링크를 텍스트로 안내하고,
Pages에 저장한 원본 식단표를 카카오 `simpleImage`로 함께 보냅니다.
식당 이름은 등록된 별칭과 한 글자 오타까지 제한적으로 보정합니다. 요일만
입력하면 오늘을 포함해 가장 가까운 해당 요일을 사용하며, `지난주`,
`이번주`, `다음주`를 함께 입력하면 그 주의 요일을 사용합니다.

## 운영상 주의

- 식단은 고려대학교 공식 페이지 내용을 그대로 전달하며 실제 메뉴는 변경될 수 있습니다.
- `generatedAt`과 공식 원문 링크를 응답에 포함합니다.
- 사용자의 개인정보를 저장하지 않습니다.
- 무료 플랜 한도 초과 시 추가 과금 대신 요청이 실패합니다.
- Worker의 비밀값은 저장소나 `wrangler.jsonc`에 넣지 않습니다.

## 무과금·보안 가드레일

- 이 봇 전용 Cloudflare 계정을 만들고 결제수단을 등록하지 않습니다.
- 배포 직전에 `npx wrangler whoami`와 대시보드에서 `Workers Free`를 확인합니다.
- Workers Paid, R2, KV, D1, Workers AI, Browser, Queue, Durable Objects를
  활성화하지 않습니다.
- GitHub 저장소는 공개 저장소로 두고 표준 `ubuntu-latest` runner만 사용합니다.
- GitHub 개인 계정의 `Settings → Billing and licensing → Budgets and alerts`에서
  Actions 제품 예산을 **화면이 허용하는 최소 금액**으로 만듭니다. GitHub의
  현재 공식 문서상 개인 계정(user-level) 예산은 한도 도달 시 항상
  하드스톱됩니다. `Stop usage when budget limit is reached`가 별도로 보이는
  계정 범위에서는 반드시 체크합니다.
- 같은 화면의 `Included usage alerts`에서 90%/100% 이메일 알림을 켭니다.
- GitHub 결제수단을 등록하지 않는 것이 가장 강한 추가 방어입니다.
- 모든 GitHub Actions는 이동 가능한 버전 태그가 아닌 전체 커밋 SHA에
  고정되어 있습니다.
- Worker는 비밀값이 없으면 열리지 않으며, 요청은 32 KiB, 식단 JSON은
  256 KiB를 넘으면 거부합니다.
- Wrangler에도 `KAKAO_SKILL_SECRET`을 필수 secret으로 선언해, 누락 시
  CLI 배포 단계에서 한 번 더 중단합니다.
- `/health`도 동일한 비밀 헤더가 필요합니다.
- 버전별 Preview URL은 비활성화해 운영 `workers.dev` 주소만 노출합니다.
- `npm run deploy`는 먼저 무료 전용 사전검사를 실행합니다. 플레이스홀더,
  유료 가능 바인딩, 커스텀 route, Paid 전용 CPU `limits` 설정이 있으면
  배포를 중단합니다. Free 플랜의 요청당 CPU 10ms 제한은 Cloudflare가
  자체적으로 강제합니다. 이 검사는 Cloudflare 계정의 실제 요금제를 대신
  확인하지 못하므로 대시보드의 `Workers Free` 확인은 여전히 필수입니다.
