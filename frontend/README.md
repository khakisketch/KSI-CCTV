# KSI-CCTV 웹 콘솔 (frontend)

Next.js 16 · React 19 · Tailwind CSS 4 기반의 KSI-CCTV 운영 콘솔입니다.
브라우저는 FastAPI 엔진에 직접 붙지 않고, Next 서버의 same-origin API 프록시를 통해
이벤트·카메라·모델·보고서를 읽고 HLS를 재생합니다.

전체 프로젝트 소개·아키텍처는 상위 [`README.md`](../README.md),
새 컴퓨터 셋업은 [`docs/development-handoff.md`](../docs/development-handoff.md)를 참고하세요.

## 빠른 실행 (권장: 저장소 루트에서 풀스택)

```bash
set -a; source .env; set +a
KSI_CCTV_DEMO=1 bash scripts/run_web.sh
```

- 웹 콘솔: <http://127.0.0.1:3100>
- 엔진 API: `127.0.0.1:8181`, HLS 게이트웨이: `127.0.0.1:8910`
- `run_web.sh`가 엔진·게이트웨이·Next 프로덕션 빌드/서빙·인증 가드를 함께 처리합니다.

## 프론트만 개발

```bash
cd frontend
npm ci
cp .env.example .env.local
# KSI_CCTV_ENGINE_URL / KSI_CCTV_API_KEY / KSI_CCTV_GATEWAY_URL 값을 로컬 환경에 맞게 수정
npm run dev
```

- 기본 개발 서버: <http://localhost:3000>
- 엔진을 `scripts/run_web.sh`로 띄웠다면 `.env.local`의 엔진 주소를
  `http://127.0.0.1:8181`로 맞춘다.
- `.env.local`은 커밋하지 않는다. 서버 전용 값이며 `NEXT_PUBLIC_` 접두사를 쓰지 않는다.

## 검증

```bash
npm run lint          # ESLint
npm test              # Vitest (63 files / 351 tests 기준)
npm run build         # TypeScript + 라우트 생성 + standalone 번들
npm run test:e2e      # Playwright — 먼저 npm run build 필요
```

추가 스크립트:

```bash
npm run test:auth-boundary   # 개발 인증 경계 회귀
npm run test:local-auth-reset
npm run test:standalone      # standalone 런타임 가드
```

E2E는 `e2e/mock-engine.mjs`·`e2e/mock-mediamtx.mjs`를 자체 기동하고
전용 포트(4175–4177)를 사용합니다. 헤드리스 Chromium은 H.264가 없어 HLS 화면 검증은
실제 Chrome/Firefox로 확인해야 합니다.

## 구조

```text
src/app/            App Router 페이지 (live·events·cameras·reports·settings·audit …)
src/app/api/        엔진 프록시·인증·HLS same-origin 프록시 라우트
src/components/     콘솔 UI 컴포넌트
src/lib/            엔진 클라이언트·역할/RBAC·이벤트 검증·포맷 유틸
src/test/           Vitest 단위/컴포넌트 테스트
e2e/                Playwright 시나리오와 mock 엔진/게이트웨이
proxy.ts            인증·역할 1차 게이트 (Next 16 proxy 규약)
```

## 인증·보안 주의

- `KSI_CCTV_WEB_PASSWORD`를 설정하면 환경변수 비밀번호가 우선하며 UI에서 변경할 수 없다.
- 비워 두면 콘솔은 공개 상태로 시작한다. 최초 보호 설정에는 32자 이상
  `KSI_CCTV_WEB_SETUP_TOKEN`이 필요하다.
- `KSI_CCTV_DEV_AUTH_BYPASS=1`은 `KSI_CCTV_WEB_DEV=1` + loopback에서
  `scripts/run_web.sh`로 띄울 때만 동작한다. 직접 `next dev`/`next start`/Docker에서는 거부된다.
- 원격(LAN/VPN/Tailscale)에서 볼 때는 bypass를 쓰지 말고 비밀번호·HTTPS를 사용한다.

## 라이선스

애플리케이션 코드는 상위 `LICENSE`(MIT)를 따른다. 제3자 모델·컨테이너 구성요소는
각 원저작자 라이선스를 따른다.
