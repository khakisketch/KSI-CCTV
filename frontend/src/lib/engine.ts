/**
 * 엔진 API 서버사이드 클라이언트 (P4b).
 *
 * **브라우저에 API 키를 노출하지 않는다** — 이 모듈은 Server Component / route handler에서만
 * 호출하고, KSI_CCTV_API_KEY(비-NEXT_PUBLIC)는 Next 서버 프로세스에만 존재한다.
 * 엔진 미연결/오류 시 null을 반환해 화면이 graceful하게 "미연결"을 표시하도록 한다.
 */

const ENGINE_URL = process.env.KSI_CCTV_ENGINE_URL ?? "http://127.0.0.1:8000";
const API_KEY = process.env.KSI_CCTV_API_KEY ?? "";

export async function engineGet<T>(path: string): Promise<T | null> {
  try {
    const res = await fetch(`${ENGINE_URL}${path}`, {
      headers: API_KEY ? { "X-API-Key": API_KEY } : {},
      cache: "no-store",
      // 엔진이 느리거나 죽었을 때 화면 렌더를 오래 막지 않도록 타임아웃
      signal: AbortSignal.timeout(4000),
    });
    if (!res.ok) return null;
    return (await res.json()) as T;
  } catch {
    return null; // 미연결/타임아웃/파싱오류 → 호출측에서 미연결 상태로 처리
  }
}

export interface EngineWriteResult<T> {
  ok: boolean;
  status: number;
  data: T | null;
}

/** 엔진 쓰기(POST/PUT) — route handler에서만 호출(API 키 서버 전용). 엔진의 4xx detail을 그대로 전달. */
export async function engineWrite<T>(
  path: string,
  body: unknown,
  method: "POST" | "PUT" | "DELETE" = "POST",
): Promise<EngineWriteResult<T>> {
  try {
    const res = await fetch(`${ENGINE_URL}${path}`, {
      method,
      headers: {
        "Content-Type": "application/json",
        ...(API_KEY ? { "X-API-Key": API_KEY } : {}),
      },
      body: method === "DELETE" ? undefined : JSON.stringify(body),
      cache: "no-store",
      signal: AbortSignal.timeout(8000),
    });
    const data = (await res.json().catch(() => null)) as T | null;
    return { ok: res.ok, status: res.status, data };
  } catch {
    return { ok: false, status: 0, data: null };
  }
}

// ── API 응답 타입(엔진 src/api 스키마와 일치) ───────────────────────────
export interface EventItem {
  event_id?: string;
  camera_id?: string;
  event_type?: string;
  timestamp?: string;
  confidence?: number;
  description?: string;
  alert_level?: string;
}
export interface ModelItem {
  name: string;
  type?: string;
  source?: string;
  enabled: boolean;
  class_profile?: string;
  classes?: string[] | null;
  description?: string;
}
export interface CameraItem {
  id?: string;
  name?: string;
  location?: string;
  enabled?: boolean;
  detection_type?: string;
  stream_url?: string;
}

export const getEvents = (limit = 100) =>
  engineGet<{ count: number; events: EventItem[] }>(`/api/events?limit=${limit}`);
export const getModels = () => engineGet<{ count: number; models: ModelItem[] }>("/api/models");
export const getCameras = () => engineGet<{ count: number; cameras: CameraItem[] }>("/api/cameras");
export const getConfig = () => engineGet<Record<string, unknown>>("/api/config");
export const getPolicies = () =>
  engineGet<{ profiles: Record<string, unknown[]> }>("/api/policies");

export interface CaptureImage {
  name: string;
  encrypted: boolean;
}
export interface CaptureCamera {
  camera_id: string;
  count: number;
  images: CaptureImage[];
}
export interface CaptureDate {
  date: string;
  cameras: CaptureCamera[];
}
export const getCaptures = () => engineGet<{ dates: CaptureDate[] }>("/api/captures");

export interface AuditEntry {
  timestamp?: string;
  event?: string;
  source?: string;
  [key: string]: unknown;
}
export const getAudit = (limit = 200) =>
  engineGet<{ count: number; entries: AuditEntry[] }>(`/api/audit?limit=${limit}`);

export interface AlertPreferences {
  email_alert_level_threshold: string;
  per_event_email_enabled: Record<string, boolean>;
}
export const getAlertPreferences = () => engineGet<AlertPreferences>("/api/alert-preferences");
