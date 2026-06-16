import type { NextConfig } from "next";
import path from "node:path";

// 모노레포가 아니므로 워크스페이스 루트를 frontend/로 고정한다. 이게 없으면 Next가
// 상위 디렉토리(홈 등)의 lockfile을 보고 루트를 잘못 추론해, standalone 산출물이
// .next/standalone/<절대경로미러>/server.js 로 깊게 중첩된다(Docker COPY가 깨짐).
const projectRoot = path.resolve();

const nextConfig: NextConfig = {
  // 설치형 Docker 패키징 — 최소 런타임 번들(.next/standalone)로 작은 이미지 + 빠른 기동.
  output: "standalone",
  outputFileTracingRoot: projectRoot,
  turbopack: { root: projectRoot },
};

export default nextConfig;
