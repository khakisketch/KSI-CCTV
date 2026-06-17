import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

// ISO 타임스탬프를 한국시간(Asia/Seoul)으로 "YYYY-MM-DD HH:mm:ss" 표시.
// 고정 타임존이라 SSR/클라가 같은 문자열을 만들어 hydration mismatch가 없고, 운영자(한국)에게
// UTC가 아닌 현지 시각을 보여준다(검증 중 UTC raw 표시 발견 → 보정).
const _tsFmt = new Intl.DateTimeFormat("sv-SE", {
  timeZone: "Asia/Seoul",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
})

export function formatTs(ts?: string): string {
  if (!ts) return "—"
  const d = new Date(ts)
  if (Number.isNaN(d.getTime())) return ts.replace("T", " ").slice(0, 19) // 파싱 실패 시 원문 폴백
  return _tsFmt.format(d).replace(/ /g, " ")
}
