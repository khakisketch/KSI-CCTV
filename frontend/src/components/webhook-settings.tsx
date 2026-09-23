"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { isMaskedSecret, maskWebhookEndpoint, shouldConfirmChannelsSave } from "@/lib/engine";

// 1회성 secret 자동 만료 TTL — 생성·회전 평문이 화면에 무기한 남지 않도록(M2).
export const ONE_TIME_TTL_MS = 60_000;

// Webhook 수신 엔드포인트 관리 + 이벤트×채널 매트릭스 (P2-T4).
// T3 API 8종을 직접 호출하지 않고, settings/page.tsx의 서버 액션(키 미노출)을
// actions prop으로 주입받아 호출한다. 테스트에서는 mock actions를 주입한다.
export interface WebhookEndpointView {
  id: string;
  name: string;
  url: string;
  secret: string;
  enabled: boolean;
  created_at: string;
  rotated_at: string | null;
}

export interface WebhookActionResult<T = unknown> {
  ok: boolean;
  data?: T | null;
  detail?: string;
}

// 액션 입출력은 T3 백엔드 실형태를 그대로 따른다(백엔드 변경 금지).
// 생성·회전 응답: {endpoint: {..., secret: 평문}} — 분리형 {endpoint, secret}이 아님(C1).
// 테스트 발송 응답: {ok: 전달 성공 여부, endpoint_id} — HTTP ok와 별개(I1).
// 매트릭스 저장 응답: {channels: 저장된 매트릭스, event_count}(M1 동기화용).
export interface WebhookSettingsActions {
  createEndpoint: (input: {
    name: string;
    url: string;
  }) => Promise<WebhookActionResult<{ endpoint: WebhookEndpointView }>>;
  updateEndpoint: (
    id: string,
    patch: { name?: string; url?: string; enabled?: boolean },
  ) => Promise<WebhookActionResult<{ endpoint: WebhookEndpointView }>>;
  deleteEndpoint: (id: string) => Promise<WebhookActionResult>;
  rotateEndpoint: (
    id: string,
  ) => Promise<WebhookActionResult<{ endpoint: WebhookEndpointView }>>;
  testEndpoint: (
    id: string,
  ) => Promise<WebhookActionResult<{ ok: boolean; endpoint_id: string }>>;
  saveChannels: (
    channels: Record<string, string[]>,
    confirm: boolean,
  ) => Promise<WebhookActionResult<{ event_count: number; channels: Record<string, string[]> }>>;
}

export interface WebhookSettingsEvent {
  name: string;
  alert_level: string;
}

const CHANNELS = ["email", "webhook"] as const;
const CHANNEL_LABEL: Record<string, string> = { email: "이메일", webhook: "Webhook" };
// 미등록 타입의 실효 기본값 — T2 `_should_send_*`와 동일(기본 허용).
const DEFAULT_CHANNELS = ["email", "webhook"];

function failureMessage(detail: string | undefined, fallback: string): string {
  return detail && detail.length > 0 ? detail : fallback;
}

export function WebhookSettings({
  initialEndpoints,
  initialChannels,
  events,
  actions,
  // 테스트에서만 단축 TTL 주입용. 기본 60초.
  oneTimeTtlMs = ONE_TIME_TTL_MS,
}: {
  initialEndpoints: WebhookEndpointView[];
  // null = channels 조회 실패 — 에러 표시 + 저장 차단(I2). {} 폴백 렌더 금지.
  initialChannels: Record<string, string[]> | null;
  events: WebhookSettingsEvent[];
  actions: WebhookSettingsActions;
  oneTimeTtlMs?: number;
}) {
  const channelsFailed = initialChannels === null;
  // H1 가드: /api/webhooks 비정상 형상({}·null·endpoints 부재)에서도 렌더 유지.
  const safeInitialEndpoints = Array.isArray(initialEndpoints) ? initialEndpoints : [];
  const [endpoints, setEndpoints] = useState<WebhookEndpointView[]>(safeInitialEndpoints);
  const [channels, setChannels] = useState<Record<string, string[]>>(initialChannels ?? {});
  // 마지막 서버 확정값 — 저장 실패 시 롤백 대상(M5).
  const [savedChannels, setSavedChannels] = useState<Record<string, string[]>>(
    initialChannels ?? {},
  );
  const [busy, setBusy] = useState<string | null>(null);
  const [matrixBusy, setMatrixBusy] = useState(false);
  const [newName, setNewName] = useState("");
  const [newUrl, setNewUrl] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [editUrl, setEditUrl] = useState("");
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [oneTime, setOneTime] = useState<{ name: string; secret: string } | null>(null);

  // M2: 1회성 secret 자동 만료 + 언마운트/교체 시 타이머 정리.
  useEffect(() => {
    if (!oneTime) return;
    const timer = setTimeout(() => setOneTime(null), oneTimeTtlMs);
    return () => clearTimeout(timer);
  }, [oneTime, oneTimeTtlMs]);

  const rows = [...new Set([...events.map((e) => e.name), ...Object.keys(channels)])];
  const levelOf = (name: string) => events.find((e) => e.name === name)?.alert_level ?? "";
  const effective = (name: string): string[] => channels[name] ?? DEFAULT_CHANNELS;
  const isListed = (name: string) => Object.prototype.hasOwnProperty.call(channels, name);

  function toggleChannel(name: string, channel: string) {
    setChannels((prev) => {
      const base = [...(prev[name] ?? DEFAULT_CHANNELS)];
      const next = base.includes(channel)
        ? base.filter((c) => c !== channel)
        : [...base, channel];
      return { ...prev, [name]: next };
    });
  }

  async function runEndpoint(id: string, label: string, fn: () => Promise<void>) {
    setBusy(`${label}:${id}`);
    try {
      await fn();
    } finally {
      setBusy(null);
    }
  }

  function showOneTime(name: string, secret: string) {
    // 생성·회전 응답의 평문 secret만 1회 표시 대상으로 삼는다.
    if (!isMaskedSecret(secret) && secret.length > 0) {
      setOneTime({ name, secret });
    }
  }

  async function create() {
    const name = newName.trim();
    const url = newUrl.trim();
    if (!name || !url) {
      toast.error("이름과 URL을 모두 입력하세요.");
      return;
    }
    setBusy("create");
    try {
      const r = await actions.createEndpoint({ name, url });
      if (r.ok && r.data) {
        // C1: 평문은 중첩 endpoint.secret에서 읽는다. C2: 목록에는 마스킹만 저장.
        const ep = r.data.endpoint;
        setEndpoints((prev) => [...prev, maskWebhookEndpoint(ep)]);
        setNewName("");
        setNewUrl("");
        showOneTime(ep.name, ep.secret);
        toast.success("엔드포인트를 추가했습니다.");
      } else {
        toast.error(failureMessage(r.detail, "추가 실패"));
      }
    } catch {
      toast.error("추가 요청을 보낼 수 없습니다. 네트워크 연결을 확인한 뒤 다시 시도하세요.");
    } finally {
      setBusy(null);
    }
  }

  async function saveEdit(id: string) {
    const name = editName.trim();
    const url = editUrl.trim();
    if (!name || !url) {
      toast.error("이름과 URL을 모두 입력하세요.");
      return;
    }
    await runEndpoint(id, "edit", async () => {
      try {
        const r = await actions.updateEndpoint(id, { name, url });
        if (r.ok && r.data) {
          // 수정 응답은 마스킹이지만 저장 전 치환으로 계약을 강제한다(C2).
          setEndpoints((prev) =>
            prev.map((e) => (e.id === id ? maskWebhookEndpoint(r.data!.endpoint) : e)),
          );
          setEditingId(null);
          toast.success("엔드포인트를 수정했습니다.");
        } else {
          toast.error(failureMessage(r.detail, "수정 실패"));
        }
      } catch {
        toast.error("수정 요청을 보낼 수 없습니다. 네트워크 연결을 확인한 뒤 다시 시도하세요.");
      }
    });
  }

  async function toggleEnabled(ep: WebhookEndpointView) {
    await runEndpoint(ep.id, "enabled", async () => {
      try {
        const r = await actions.updateEndpoint(ep.id, { enabled: !ep.enabled });
        if (r.ok && r.data) {
          setEndpoints((prev) =>
            prev.map((e) => (e.id === ep.id ? maskWebhookEndpoint(r.data!.endpoint) : e)),
          );
          toast.success(ep.enabled ? "엔드포인트를 비활성화했습니다." : "엔드포인트를 활성화했습니다.");
        } else {
          toast.error(failureMessage(r.detail, "상태 변경 실패"));
        }
      } catch {
        toast.error("상태 변경 요청을 보낼 수 없습니다. 네트워크 연결을 확인한 뒤 다시 시도하세요.");
      }
    });
  }

  async function remove(ep: WebhookEndpointView) {
    await runEndpoint(ep.id, "delete", async () => {
      try {
        const r = await actions.deleteEndpoint(ep.id);
        if (r.ok) {
          setEndpoints((prev) => prev.filter((e) => e.id !== ep.id));
          setConfirmDeleteId(null);
          toast.success("엔드포인트를 삭제했습니다.");
        } else {
          toast.error(failureMessage(r.detail, "삭제 실패"));
        }
      } catch {
        toast.error("삭제 요청을 보낼 수 없습니다. 네트워크 연결을 확인한 뒤 다시 시도하세요.");
      }
    });
  }

  async function rotate(ep: WebhookEndpointView) {
    await runEndpoint(ep.id, "rotate", async () => {
      try {
        const r = await actions.rotateEndpoint(ep.id);
        if (r.ok && r.data) {
          // C1: 평문은 중첩 endpoint.secret에서 읽는다. C2: 목록에는 마스킹만 저장.
          const next = r.data.endpoint;
          setEndpoints((prev) =>
            prev.map((e) => (e.id === ep.id ? maskWebhookEndpoint(next) : e)),
          );
          showOneTime(next.name, next.secret);
          toast.success("secret을 회전했습니다. 새 secret을 안전한 곳에 보관하세요.");
        } else {
          toast.error(failureMessage(r.detail, "회전 실패"));
        }
      } catch {
        toast.error("회전 요청을 보낼 수 없습니다. 네트워크 연결을 확인한 뒤 다시 시도하세요.");
      }
    });
  }

  async function testSend(ep: WebhookEndpointView) {
    await runEndpoint(ep.id, "test", async () => {
      try {
        const r = await actions.testEndpoint(ep.id);
        // I1: HTTP ok와 전달 성공(data.ok)은 별개 — data.ok===false면 실패 토스트.
        if (r.ok && r.data) {
          if (r.data.ok) {
            toast.success(`테스트 발송 성공 — ${ep.name}에서 수신을 확인하세요.`);
          } else {
            toast.error(`테스트 발송 실패 — ${ep.name}에서 응답을 받지 못했습니다. URL·수신 서버를 확인하세요.`);
          }
        } else {
          toast.error(failureMessage(r.detail, "테스트 발송 실패"));
        }
      } catch {
        toast.error("테스트 요청을 보낼 수 없습니다. 네트워크 연결을 확인한 뒤 다시 시도하세요.");
      }
    });
  }

  async function copySecret() {
    if (!oneTime) return;
    try {
      const withClipboard = navigator as Navigator & {
        clipboard?: { writeText: (text: string) => Promise<void> };
      };
      if (!withClipboard.clipboard) throw new Error("clipboard unavailable");
      await withClipboard.clipboard.writeText(oneTime.secret);
      toast.success("secret을 클립보드에 복사했습니다.");
    } catch {
      toast.error("복사에 실패했습니다. 화면의 secret을 직접 복사하세요.");
    }
  }

  async function saveMatrix() {
    // I2: 조회 실패 상태에서는 저장 자체를 차단 — {} 저장이 진실을 덮어쓰지 않도록.
    if (channelsFailed) {
      toast.error("채널 매트릭스를 불러오지 못해 저장할 수 없습니다. 새로고침 후 다시 시도하세요.");
      return;
    }
    setMatrixBusy(true);
    try {
      const confirm = shouldConfirmChannelsSave(channels);
      const r = await actions.saveChannels(channels, confirm);
      if (r.ok && r.data) {
        // M1: 서버 확정값으로 동기화.
        setChannels(r.data.channels);
        setSavedChannels(r.data.channels);
        toast.success("채널 매트릭스를 저장했습니다 (파이프라인 재시작 후 적용).");
      } else {
        // M5: 저장 실패 시 마지막 서버값으로 롤백.
        setChannels(savedChannels);
        toast.error(failureMessage(r.detail, "매트릭스 저장 실패"));
      }
    } catch {
      setChannels(savedChannels);
      toast.error("저장 요청을 보낼 수 없습니다. 네트워크 연결을 확인한 뒤 다시 저장하세요.");
    } finally {
      setMatrixBusy(false);
    }
  }

  const endpointBusy = (id: string) => busy !== null && busy.endsWith(`:${id}`);
  // M3: 어떤 엔드포인트 작업이 진행 중이면 추가 폼도 잠근다(중복 생성 방지).
  const formBusy = busy !== null;
  // I2: 조회 실패 시 매트릭스 조작 전체 차단.
  const matrixLocked = matrixBusy || channelsFailed;

  return (
    <div className="space-y-6">
      {/* ── 엔드포인트 목록·추가·수정·비활성·회전·테스트 ── */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <p className="text-sm font-medium">Webhook 엔드포인트</p>
          <Badge variant="secondary">{endpoints.filter((e) => e.enabled).length}/{endpoints.length} 활성</Badge>
        </div>
        <p className="text-xs text-muted-foreground" style={{ wordBreak: "keep-all" }}>
          secret은 생성·회전 응답에서만 1회 표시되며, 저장 후에는 재조회할 수 없습니다.
        </p>

        {oneTime && (
          <div className="rounded-md border border-amber-500/50 bg-amber-500/10 p-3 text-sm">
            <p className="font-medium">[{oneTime.name}] 1회성 secret — 1회만 표시됩니다</p>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <code className="break-all font-mono text-xs">{oneTime.secret}</code>
              <Button size="sm" variant="outline" onClick={copySecret}>
                secret 복사
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setOneTime(null)}>
                숨기기
              </Button>
            </div>
          </div>
        )}

        {endpoints.length === 0 ? (
          <p className="text-sm text-muted-foreground">등록된 엔드포인트가 없습니다.</p>
        ) : (
          <ul className="space-y-2">
            {endpoints.map((ep) => (
              <li key={ep.id} className="rounded-md border p-3 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">{ep.name}</span>
                  <Badge variant={ep.enabled ? "success" : "warning"}>
                    {ep.enabled ? "활성" : "비활성"}
                  </Badge>
                  <span className="text-xs text-muted-foreground">{`secret: ${ep.secret}`}</span>
                </div>
                <p className="mt-1 break-all text-xs text-muted-foreground">{ep.url}</p>
                {editingId === ep.id ? (
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <label className="text-xs" htmlFor={`webhook-edit-name-${ep.id}`}>이름</label>
                    <input
                      id={`webhook-edit-name-${ep.id}`}
                      className="rounded border px-2 py-1 text-sm"
                      value={editName}
                      disabled={endpointBusy(ep.id)}
                      onChange={(e) => setEditName(e.target.value)}
                    />
                    <label className="text-xs" htmlFor={`webhook-edit-url-${ep.id}`}>URL</label>
                    <input
                      id={`webhook-edit-url-${ep.id}`}
                      className="rounded border px-2 py-1 text-sm"
                      value={editUrl}
                      disabled={endpointBusy(ep.id)}
                      onChange={(e) => setEditUrl(e.target.value)}
                    />
                    <Button size="sm" disabled={endpointBusy(ep.id)} onClick={() => saveEdit(ep.id)}>
                      저장
                    </Button>
                    <Button size="sm" variant="ghost" disabled={endpointBusy(ep.id)} onClick={() => setEditingId(null)}>
                      취소
                    </Button>
                  </div>
                ) : (
                  <div className="mt-2 flex flex-wrap gap-2">
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={endpointBusy(ep.id)}
                      onClick={() => {
                        setEditingId(ep.id);
                        setEditName(ep.name);
                        setEditUrl(ep.url);
                      }}
                    >
                      수정
                    </Button>
                    <Button size="sm" variant="outline" disabled={endpointBusy(ep.id)} onClick={() => toggleEnabled(ep)}>
                      {ep.enabled ? "비활성화" : "활성화"}
                    </Button>
                    <Button size="sm" variant="outline" disabled={endpointBusy(ep.id)} onClick={() => rotate(ep)}>
                      secret 회전
                    </Button>
                    <Button size="sm" variant="outline" disabled={endpointBusy(ep.id) || !ep.enabled} onClick={() => testSend(ep)}>
                      테스트 발송
                    </Button>
                    {confirmDeleteId === ep.id ? (
                      <>
                        <span className="text-xs text-muted-foreground">정말 삭제할까요?</span>
                        <Button size="sm" variant="destructive" disabled={endpointBusy(ep.id)} onClick={() => remove(ep)}>
                          삭제 확인
                        </Button>
                        <Button size="sm" variant="ghost" disabled={endpointBusy(ep.id)} onClick={() => setConfirmDeleteId(null)}>
                          취소
                        </Button>
                      </>
                    ) : (
                      <Button size="sm" variant="destructive" disabled={endpointBusy(ep.id)} onClick={() => setConfirmDeleteId(ep.id)}>
                        삭제
                      </Button>
                    )}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}

        <div className="flex flex-wrap items-end gap-2 border-t pt-3">
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground" htmlFor="webhook-new-name">이름</label>
            <input
              id="webhook-new-name"
              className="block rounded border px-2 py-1 text-sm"
              placeholder="관제실"
              value={newName}
              disabled={formBusy}
              onChange={(e) => setNewName(e.target.value)}
            />
          </div>
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground" htmlFor="webhook-new-url">URL</label>
            <input
              id="webhook-new-url"
              className="block rounded border px-2 py-1 text-sm"
              placeholder="https://hooks.example.com/ksi"
              value={newUrl}
              disabled={formBusy}
              onChange={(e) => setNewUrl(e.target.value)}
            />
          </div>
          <Button size="sm" disabled={formBusy} onClick={create}>
            엔드포인트 추가
          </Button>
        </div>
        <p className="text-xs text-muted-foreground" style={{ wordBreak: "keep-all" }}>
          URL은 http/https만 허용되며 자격정보 포함 URL은 거부됩니다.
        </p>
      </div>

      {/* ── 이벤트×채널 매트릭스 ── */}
      <div className="space-y-2 border-t pt-4">
        <div className="flex items-center justify-between">
          <p className="text-sm font-medium">이벤트 × 채널 매트릭스</p>
          <Badge variant="secondary">{rows.length} 타입</Badge>
        </div>
        <p className="text-xs text-muted-foreground" style={{ wordBreak: "keep-all" }}>
          매트릭스에 등록되지 않은 이벤트 타입은 기본 허용됩니다
          (임계값·활성 엔드포인트 조건 충족 시 발송).
          이메일은 기존 per-event 설정에서 끈 타입은 매트릭스와 무관하게 차단됩니다.
        </p>
        {channelsFailed && (
          <p className="rounded-md border border-destructive/50 bg-destructive/10 p-2 text-xs text-destructive" style={{ wordBreak: "keep-all" }}>
            채널 매트릭스를 불러오지 못했습니다. 새로고침 후 다시 시도하세요 — 복구 전까지 저장은 차단됩니다.
          </p>
        )}
        <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
          {rows.map((name) => (
            <div
              key={name}
              className="flex items-center gap-3 rounded border px-2 py-1.5 text-sm"
              style={{ wordBreak: "keep-all" }}
            >
              <span className="min-w-0 flex-1 truncate">{name}</span>
              {levelOf(name) && <Badge variant="outline">{levelOf(name)}</Badge>}
              {!isListed(name) && <Badge variant="secondary">기본 허용</Badge>}
              {CHANNELS.map((ch) => (
                <label key={ch} className="flex items-center gap-1 text-xs text-muted-foreground">
                  <input
                    type="checkbox"
                    className="h-4 w-4 accent-primary"
                    aria-label={`${name} ${ch}`}
                    checked={effective(name).includes(ch)}
                    disabled={matrixLocked}
                    onChange={() => toggleChannel(name, ch)}
                  />
                  {CHANNEL_LABEL[ch]}
                </label>
              ))}
            </div>
          ))}
        </div>
        {rows.length === 0 && (
          <p className="text-sm text-muted-foreground">표시할 이벤트 타입이 없습니다.</p>
        )}
        <Button onClick={saveMatrix} disabled={matrixLocked} size="sm">
          {matrixBusy ? "저장 중…" : "매트릭스 저장"}
        </Button>
      </div>
    </div>
  );
}
