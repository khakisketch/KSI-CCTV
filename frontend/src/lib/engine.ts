/**
 * 엔진 API 서버사이드 클라이언트 (P4b).
 *
 * **브라우저에 API 키를 노출하지 않는다** — 이 모듈은 Server Component / route handler에서만
 * 호출하고, KSI_CCTV_API_KEY(비-NEXT_PUBLIC)는 Next 서버 프로세스에만 존재한다.
 * 엔진 미연결/오류 시 null을 반환해 화면이 graceful하게 "미연결"을 표시하도록 한다.
 */

import type { CameraZone } from "@/lib/cameras";

const ENGINE_URL = process.env.KSI_CCTV_ENGINE_URL ?? "http://127.0.0.1:8000";
const API_KEY = process.env.KSI_CCTV_API_KEY ?? "";

export interface EngineGetResult<T> { ok: boolean; status: number; data: T | null; }
export function engineGet<T>(path: string): Promise<T | null>;
export function engineGet<T>(path: string, options: { includeStatus: true }): Promise<EngineGetResult<T>>;
export async function engineGet<T>(path: string, options?: { includeStatus: true }): Promise<T | null | EngineGetResult<T>> {
  try {
    const res = await fetch(`${ENGINE_URL}${path}`, {
      headers: API_KEY ? { "X-API-Key": API_KEY } : {},
      cache: "no-store",
      // 엔진이 느리거나 죽었을 때 화면 렌더를 오래 막지 않도록 타임아웃
      signal: AbortSignal.timeout(4000),
    });
    const data = (await res.json().catch(() => null)) as T | null;
    return options?.includeStatus ? { ok: res.ok, status: res.status, data } : res.ok ? data : null;
  } catch {
    return options?.includeStatus ? { ok: false, status: 0, data: null } : null; // 미연결/타임아웃/파싱오류 → 호출측에서 미연결 상태로 처리
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
  timeoutMs: number = 8000,
  actor?: string,
): Promise<EngineWriteResult<T>> {
  try {
    const res = await fetch(`${ENGINE_URL}${path}`, {
      method,
      headers: {
        "Content-Type": "application/json",
        ...(API_KEY ? { "X-API-Key": API_KEY } : {}),
        ...(actor ? { "X-KSI-Actor": actor } : {}),
      },
      body: method === "DELETE" ? undefined : JSON.stringify(body),
      cache: "no-store",
      signal: AbortSignal.timeout(timeoutMs),
    });
    const data = (await res.json().catch(() => null)) as T | null;
    return { ok: res.ok, status: res.status, data };
  } catch {
    return { ok: false, status: 0, data: null };
  }
}

/** Server-only binary response proxy for camera media. */
export async function engineFetch(path: string): Promise<Response | null> {
  try {
    return await fetch(`${ENGINE_URL}${path}`, {
      headers: API_KEY ? { "X-API-Key": API_KEY } : {},
      cache: "no-store",
      signal: AbortSignal.timeout(8000),
    });
  } catch {
    return null;
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
  alert_state?: string;
  source_models?: string[];
  rule_name?: string;
  // 2차 VLM 정밀분석 결과(백엔드가 이미 서빙 — 표시만 확장)
  vlm_risk_level?: string;
  vlm_confidence?: number;
  vlm_detected_events?: string[];
  vlm_recommended_action?: string;
  vlm_unconfirmed?: boolean;
  verification_summary?: Record<string, unknown>;
  verification_reason_codes?: string[];
  verification_required?: boolean;
  verification_mode?: string;
  verification_state?: "not_required" | "pending" | "confirmed" | "rejected" | "unverified" | string;
  verification_quorum?: number;
  verification_attempts?: number;
  frame_count?: number;
  confirm_count?: number;
  reject_count?: number;
  error_count?: number;
  verification_model?: string;
  verification_model_digest?: string;
  metadata?: EventTrackingMetadata;
  evidence_image?: string | null;
}
export interface EventTrackingMetadata {
  track_id?: number | string;
  zone_id?: string;
  direction_label?: string;
  dwell_sec?: number;
  action?: string;
  confidence?: number;
  model_names?: string[];
  sequence_length?: number;
}
export type IncidentStatus = "new" | "acknowledged" | "in_progress" | "resolved";
export type IncidentDisposition = "confirmed" | "false_positive" | "duplicate" | "test";
export interface IncidentActionItem {
  previous_status: IncidentStatus;
  status: IncidentStatus;
  disposition?: IncidentDisposition | null;
  note: string;
  created_at: string;
}
export interface IncidentItem {
  event_id: string;
  category?: "safety" | "camera_health";
  camera_id?: string | null;
  source_event_type?: string | null;
  source_event_alert_level?: string | null;
  status: IncidentStatus;
  disposition?: IncidentDisposition | null;
  acknowledged_at?: string | null;
  resolved_at?: string | null;
  recovered_at?: string | null;
  recovery_event_id?: string | null;
  updated_at: string;
  actions?: IncidentActionItem[];
}
export interface IncidentDetailResponse {
  incident: IncidentItem;
  actions: IncidentActionItem[];
}
export interface IncidentsQuery {
  status?: IncidentStatus;
  category?: "safety" | "camera_health";
  unresolved?: boolean;
  limit?: number;
}
export const getIncidents = (options: IncidentsQuery = {}) => {
  const params = new URLSearchParams();
  if (options.status) params.set("status", options.status);
  if (options.category) params.set("category", options.category);
  if (options.unresolved) params.set("unresolved", "true");
  if (options.limit) params.set("limit", String(options.limit));
  const query = params.size ? `?${params.toString()}` : "";
  return engineGet<{ available: boolean; count: number; incidents: IncidentItem[] }>(`/api/incidents${query}`);
};
export function parseIncidentDetail(value: unknown): IncidentDetailResponse | null {
  if (!value || typeof value !== "object") return null;
  const record = value as Record<string, unknown>;
  const incident = record.incident;
  if (!incident || typeof incident !== "object") return null;
  const item = incident as Record<string, unknown>;
  if (typeof item.event_id !== "string" || typeof item.status !== "string" || typeof item.updated_at !== "string") return null;
  if (!isIncidentStatus(item.status)) return null;
  if (item.category !== undefined && item.category !== "safety" && item.category !== "camera_health") return null;
  const actions = Array.isArray(record.actions) ? record.actions.filter(isIncidentAction) : [];
  return {
    incident: {
      event_id: item.event_id,
      status: item.status,
      updated_at: item.updated_at,
      category: item.category,
      camera_id: stringOrNull(item.camera_id),
      source_event_type: stringOrNull(item.source_event_type),
      source_event_alert_level: stringOrNull(item.source_event_alert_level),
      disposition: isDisposition(item.disposition) ? item.disposition : null,
      acknowledged_at: stringOrNull(item.acknowledged_at),
      resolved_at: stringOrNull(item.resolved_at),
      recovered_at: stringOrNull(item.recovered_at),
      recovery_event_id: stringOrNull(item.recovery_event_id),
      actions,
    },
    actions,
  };
}
function isIncidentStatus(value: unknown): value is IncidentStatus {
  return value === "new" || value === "acknowledged" || value === "in_progress" || value === "resolved";
}
function isDisposition(value: unknown): value is IncidentDisposition {
  return value === "confirmed" || value === "false_positive" || value === "duplicate" || value === "test";
}
function stringOrNull(value: unknown): string | null | undefined {
  return typeof value === "string" || value === null ? value : undefined;
}
function isIncidentAction(value: unknown): value is IncidentActionItem {
  if (!value || typeof value !== "object") return false;
  const action = value as Record<string, unknown>;
  return isIncidentStatus(action.previous_status) && isIncidentStatus(action.status) &&
    typeof action.note === "string" && typeof action.created_at === "string" &&
    (action.disposition === undefined || action.disposition === null || isDisposition(action.disposition));
}
export const getIncident = async (eventId: string) =>
  parseIncidentDetail(await engineGet<unknown>(`/api/incidents/${encodeURIComponent(eventId)}`));
export interface TrackingSnapshot {
  camera_id: string;
  timestamp: string | null;
  age_sec: number | null;
  stale: boolean;
  frame: { width: number; height: number };
  events: string[];
  tracks: Array<{
    track_id: number;
    class_name: string;
    confidence: number;
    bbox: [number, number, number, number];
    trajectory: Array<[number, number]>;
    first_seen: string;
    last_seen: string;
  }>;
  zones: Array<{
    id: string;
    type: "polygon" | "line";
    points: Array<[number, number]>;
    direction: "any" | "positive" | "negative";
  }>;
}
export interface ModelItem {
  name: string;
  type?: string;
  source?: string;
  enabled: boolean;
  class_profile?: string;
  classes?: string[] | null;
  description?: string;
  runtime?: {
    available: boolean;
    loaded?: boolean;
    error?: string | null;
    inference_count?: number;
    error_count?: number;
    last_latency_ms?: number;
  };
}
export interface CameraItem {
  id?: string;
  name?: string;
  location?: string;
  enabled?: boolean;
  detection_type?: string;
  stream_url?: string;
  enabled_events?: string[];
  credential_configured?: boolean;
  managed?: boolean;
  fps?: number;
  resolution?: [number, number] | null;
  zones?: CameraZone[];
  profile?: string | null;
}
export interface CameraDiscoveryItem {
  name?: string;
  host?: string;
  onvif_url?: string;
  rtsp_url?: string;
  model?: string;
}
export interface CameraStreamTest {
  ok?: boolean;
  connected?: boolean;
  snapshot_url?: string;
  diagnostics?: Record<string, string | number | boolean | null>;
  detail?: string;
}

export interface EventsQuery {
  limit?: number;
  event_type?: string;
  camera_id?: string;
  alert_level?: string;
  since?: string;
  until?: string;
  offset?: number;
}
export const getEvents = (q: EventsQuery | number = {}) => {
  const opts: EventsQuery = typeof q === "number" ? { limit: q } : q;
  const params = new URLSearchParams();
  if (opts.limit) params.set("limit", String(opts.limit));
  if (opts.event_type) params.set("event_type", opts.event_type);
  if (opts.camera_id) params.set("camera_id", opts.camera_id);
  if (opts.alert_level) params.set("alert_level", opts.alert_level);
  if (opts.since) params.set("since", opts.since);
  if (opts.until) params.set("until", opts.until);
  if (opts.offset) params.set("offset", String(opts.offset));
  return engineGet<{ count: number; events: EventItem[] }>(`/api/events?${params.toString()}`);
};
export async function getEvent(eventId: string): Promise<EventItem | null> {
  const response = await engineGet<{ event?: EventItem }>(`/api/events/${encodeURIComponent(eventId)}`);
  return response?.event ?? null;
}
export interface ObservationItem {
  camera_id: string;
  bucket_start: string;
  samples: number;
  latest_counts: Record<string, number>;
  max_counts: Record<string, number>;
  model_counts: Record<string, number>;
  zone_max_counts: Record<string, number>;
}
export const getObservations = (cameraId?: string, limit = 120) => {
  const params = new URLSearchParams({ limit: String(limit) });
  if (cameraId) params.set("camera_id", cameraId);
  return engineGet<{ connected: boolean; count: number; observations: ObservationItem[] }>(
    `/api/observations?${params.toString()}`,
  );
};
export const getModels = () => engineGet<{ count: number; models: ModelItem[] }>("/api/models");
export const getCameras = () => engineGet<{ count: number; cameras: CameraItem[]; revision: number }>("/api/cameras");
export interface CameraDetail { revision: number; camera: CameraItem; }
export const getCamera = (cameraId: string) =>
  engineGet<CameraDetail>(`/api/cameras/${encodeURIComponent(cameraId)}`);
export const getConfig = () => engineGet<Record<string, unknown>>("/api/config");
export const getPolicies = () =>
  engineGet<{ profiles: Record<string, unknown[]> }>("/api/policies");
export interface EventCatalogItem {
  name: string;
  label?: string;
  category?: string;
  status?: "active" | "experimental" | "planned";
  runtime?: "detection" | "health" | "tracking";
  release_qualified?: boolean;
  selectable?: boolean;
  blocked_reason?: string | null;
  reason?: string | null;
  requires_zone?: string | null;
  alert_level?: string;
  vlm?: string;
  cooldown_sec?: number;
  tracking?: {
    classes?: string[];
    min_confidence?: number;
    dwell_time_sec?: number | null;
    grace_sec?: number | null;
    min_displacement_px?: number | null;
  } | null;
  domains?: string[];
}
export const getEventCatalog = () =>
  engineGet<{
    catalog: EventCatalogItem[];
    count: number;
    profiles: Record<string, { label?: string; default_events: string[] }>;
  }>("/api/event-catalog");

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

export interface AlertDeliveryStatus {
  available: boolean;
  email_configured?: boolean;
  recipient_count?: number;
  outbox?: {
    running: boolean;
    pending: number;
    inflight: number;
    delivered: number;
    dead_letter: number;
  };
}
export const getAlertDeliveryStatus = () => engineGet<AlertDeliveryStatus>("/api/alerts/status");

// ── Webhook 수신 엔드포인트 + 이벤트×채널 매트릭스 (P2-T4, T3 API 8종 소비) ──
// secret은 생성·회전 응답에서만 1회 평문, 그 외 목록·수정 응답은 "***" 마스킹.
export interface WebhookEndpointItem {
  id: string;
  name: string;
  url: string;
  secret: string;
  enabled: boolean;
  created_at: string;
  rotated_at: string | null;
}
export const getWebhooks = () =>
  engineGet<{ count: number; endpoints: WebhookEndpointItem[] }>("/api/webhooks");
export const getWebhookChannels = () =>
  engineGet<{ channels: Record<string, string[]> }>("/api/webhooks/channels");

/** 목록 state 저장 전 마스킹 — 생성·회전 응답의 중첩 평문이 목록에 남지 않도록(C2). */
export function maskWebhookEndpoint<T extends { secret: string }>(ep: T): T {
  return { ...ep, secret: "***" };
}
/** 목록·수정 응답의 마스킹 secret인지 판정 — 평문 1회 secret과 구분용. */
export function isMaskedSecret(secret: unknown): boolean {
  return typeof secret !== "string" || secret === "***" || secret === "";
}

/** 매트릭스 저장 시 confirm:true 필요 여부 — 빈 매트릭스({} 또는 전부 빈 목록). */
export function shouldConfirmChannelsSave(channels: Record<string, string[]>): boolean {
  const keys = Object.keys(channels ?? {});
  if (keys.length === 0) return true;
  return keys.every((k) => (channels[k] ?? []).length === 0);
}

export interface SystemMetrics {
  cpu_percent?: number;
  memory_percent?: number;
  memory_used_mb?: number;
  memory_total_mb?: number;
  disk_percent?: number;
  disk_used_gb?: number;
  disk_total_gb?: number;
  gpu_percent?: number | null;
  gpu_memory_percent?: number | null;
  gpu_temperature?: number | null;
}
export interface HealthComponent {
  name: string;
  healthy: boolean;
  message?: string;
}
export interface AiRuntimeStatus {
  behavior?: {
    enabled?: boolean;
    available?: boolean;
    runtime_errors?: { pose?: number; action?: number };
    unavailable?: number;
  };
  vlm?: {
    configured_enabled?: boolean;
    enabled?: boolean;
    runtime_qualified?: boolean;
    provider_available?: boolean;
    reason_category?: string;
    queue?: {
      in_flight?: number;
      pending?: number;
      soft_limit?: number;
      hard_limit?: number;
      saturated?: boolean;
      dropped?: number;
    };
    lifecycle?: { unverified?: number; [name: string]: number | undefined };
  };
}
export interface SystemInfo {
  metrics: SystemMetrics;
  health: { status?: string; components: HealthComponent[] };
  ai_runtime?: AiRuntimeStatus;
}
export const getSystem = () => engineGet<SystemInfo>("/api/system");

export interface CameraProfile {
  domain?: string;
  enabled?: boolean;
  enabled_events: string[];
}
export const getCameraProfiles = () =>
  engineGet<{ cameras: Record<string, CameraProfile> }>("/api/camera-profiles");

export interface CameraTelemetryItem {
  connected?: boolean;
  fps?: number;
  dropped_frames?: number;
  is_stale?: boolean;
  seconds_since_last_frame?: number | null;
  health_state?: "initializing" | "receiving" | "stale" | "disconnected";
  tamper_state?: "normal" | "suspected" | "tampered";
  active_tracks?: number;
  tracking?: {
    active_tracks?: number;
    updates?: number;
    errors?: number;
    runtime_errors?: number;
    last_latency_ms?: number;
    average_latency_ms?: number;
    last_update?: string | null;
    line_crossing?: {
      candidates?: number;
      emitted?: number;
      cooldown_suppressed?: number;
      direction_suppressed?: number;
    };
    loitering?: {
      active_episodes?: number;
      emitted?: number;
      cooldown_suppressed?: number;
    };
  };
}
export type TelemetryStatus = "ok" | "unavailable" | "error";
export interface CameraTelemetryResponse {
  connected: boolean;
  // Snapshot 생성 시각. 개별 카메라의 마지막 프레임 시각과는 별개이며 API source
  // freshness를 판정하는 용도다.
  generated_at?: string;
  telemetry_status?: TelemetryStatus;
  cameras: Record<string, CameraTelemetryItem>;
}
export function isCameraReceiving(status?: CameraTelemetryItem): boolean {
  return Boolean(
    status?.connected &&
    !status.is_stale &&
    (status.health_state === undefined || status.health_state === "receiving") &&
    typeof status.seconds_since_last_frame === "number",
  );
}
export const getCameraTelemetry = () =>
  engineGet<CameraTelemetryResponse>(
    "/api/cameras/telemetry",
  );

export interface ControlStatus {
  connected: boolean;
  running: boolean;
  status?: {
    total_events?: number;
    behavior?: { enabled?: boolean; counters?: Record<string, number> };
    vlm_queue?: {
      in_flight?: number;
      pending?: number;
      dropped?: number;
      hard_limit?: number;
    };
    ai_runtime?: AiRuntimeStatus;
  };
}
export const getControlStatus = () => engineGet<ControlStatus>("/api/control/status");

export type CommissioningCheckStatus = "pass" | "warning" | "fail";
export interface CommissioningSite {
  name: string;
  location: string;
  timezone: string;
  installer: string;
  expected_camera_count: number;
}
export interface CommissioningCheck {
  id: string;
  label: string;
  status: CommissioningCheckStatus;
  summary: string;
  remediation?: string | null;
}
export interface CommissioningCamera {
  id: string;
  name: string;
  location?: string | null;
  enabled: boolean;
  health_state?: string | null;
  fps?: number | null;
  enabled_events: string[];
  zone_count: number;
  zone_warnings: string[];
  ai_events: string[];
  checks: {
    live_verified: boolean;
    reconnect_verified: boolean;
    detection_verified: boolean;
    alert_verified: boolean;
    notes?: string | null;
  };
  ready: boolean;
}
export interface CommissioningData {
  revision: number;
  site: CommissioningSite;
  completed_at?: string | null;
  handoff?: { customer_name?: string; notes?: string; accepted_at?: string | null };
  ready_to_complete: boolean;
  progress: { passed: number; total: number; percent: number };
  checks: CommissioningCheck[];
  cameras: CommissioningCamera[];
  health?: unknown;
  event_runtime?: unknown;
}
export type PreflightData = { checks?: CommissioningCheck[]; [key: string]: unknown };
export type VideoSourceCapabilities = Record<string, unknown>;
export const getCommissioning = () => engineGet<CommissioningData>("/api/commissioning");
export const getPreflight = () => engineGet<PreflightData>("/api/preflight");
export const getVideoSourceCapabilities = () => engineGet<VideoSourceCapabilities>("/api/video-sources/capabilities");
export const getCommissioningReport = () => engineGet<Record<string, unknown>>("/api/commissioning/report");
