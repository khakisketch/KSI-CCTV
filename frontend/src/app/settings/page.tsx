import { cookies } from "next/headers";

import { AlertSettings } from "@/components/alert-settings";
import { AccessProtectionSettings } from "@/components/access-protection-settings";
import { UserManagement } from "@/components/user-management";
import {
  WebhookSettings,
  type WebhookActionResult,
  type WebhookEndpointView,
  type WebhookSettingsActions,
} from "@/components/webhook-settings";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { toPublicUser } from "@/app/api/users/route";
import {
  engineWrite,
  getAlertDeliveryStatus,
  getAlertPreferences,
  getConfig,
  getPolicies,
  getWebhookChannels,
  getWebhooks,
} from "@/lib/engine";
import { getAuthStatus, SESSION_COOKIE, sessionPrincipal } from "@/lib/auth";
import { roleAtLeast } from "@/lib/roles";
import { findUserById, listUsers } from "@/lib/users";

export const dynamic = "force-dynamic";

type PolicyEvent = { name?: string; alert_level?: string };

function deriveEvents(policies: { profiles?: Record<string, unknown> } | null) {
  const seen = new Map<string, string>();
  for (const list of Object.values(policies?.profiles ?? {})) {
    for (const e of (list as PolicyEvent[]) ?? []) {
      if (e?.name && !seen.has(e.name)) seen.set(e.name, e.alert_level ?? "");
    }
  }
  return [...seen].map(([name, alert_level]) => ({ name, alert_level }));
}

function Section({ title, data }: { title: string; data: Record<string, unknown> }) {
  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center gap-2">
          <CardTitle className="text-base">{title}</CardTitle>
          <Badge variant="outline">읽기 전용</Badge>
        </div>
      </CardHeader>
      <CardContent>
        <dl className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          {Object.entries(data).map(([k, v]) => (
            <div key={k} className="flex items-center justify-between gap-4 border-b py-1.5 text-sm">
              <dt className="text-muted-foreground">{k}</dt>
              <dd className="break-all font-medium tabular-nums">
                {v === null || v === undefined ? "—" : String(v)}
              </dd>
            </div>
          ))}
        </dl>
      </CardContent>
    </Card>
  );
}

export default async function SettingsPage() {
  const [cfg, prefs, policies, delivery, authStatus, webhooks, webhookChannels] = await Promise.all([
    getConfig(),
    getAlertPreferences(),
    getPolicies(),
    getAlertDeliveryStatus(),
    getAuthStatus(),
    getWebhooks(),
    getWebhookChannels(),
  ]);
  const connected = cfg !== null;
  const events = deriveEvents(policies);
  const principal = await sessionPrincipal((await cookies()).get(SESSION_COOKIE)?.value);
  const isAdmin = principal?.role === "admin";
  const managedUsers = isAdmin ? (await listUsers()).map(toPublicUser) : [];

  // ── Webhook 관리 서버 액션 (P2-T4, webhook 카드 영역) ──
  // 별도 Next 프록시 라우트 없이 이 페이지 안에서 완결한다. API 키는 서버에만
  // 존재하고, 액션마다 admin 역할을 재확인한다(proxy 1차 게이트의 2차 방어).
  async function webhookActor(): Promise<string | null> {
    const actorPrincipal = await sessionPrincipal(
      (await cookies()).get(SESSION_COOKIE)?.value,
    );
    if (!actorPrincipal || !roleAtLeast(actorPrincipal.role, "admin")) return null;
    if (actorPrincipal.userId === undefined) return "admin";
    const user = await findUserById(actorPrincipal.userId);
    if (!user || user === "error" || !user.enabled) return "system";
    return user.username;
  }

  function engineDetail(data: unknown): string {
    if (data && typeof data === "object" && "detail" in data) {
      const detail = (data as { detail?: unknown }).detail;
      if (typeof detail === "string" && detail.length > 0) return detail;
    }
    return "엔진 연결 실패";
  }

  // T3 백엔드 실형태: 생성·회전 응답은 {endpoint: {..., secret: 평문}}(중첩).
  async function createWebhookEndpoint(input: {
    name: string;
    url: string;
  }): Promise<WebhookActionResult<{ endpoint: WebhookEndpointView }>> {
    "use server";
    const actor = await webhookActor();
    if (actor === null) return { ok: false, detail: "이 리소스에 접근할 권한이 없습니다." };
    const r = await engineWrite<{ endpoint: WebhookEndpointView }>(
      "/api/webhooks",
      input,
      "POST",
      8000,
      actor,
    );
    if (!r.ok || !r.data) return { ok: false, detail: engineDetail(r.data) };
    return { ok: true, data: r.data };
  }

  async function updateWebhookEndpoint(
    id: string,
    patch: { name?: string; url?: string; enabled?: boolean },
  ): Promise<WebhookActionResult<{ endpoint: WebhookEndpointView }>> {
    "use server";
    const actor = await webhookActor();
    if (actor === null) return { ok: false, detail: "이 리소스에 접근할 권한이 없습니다." };
    const r = await engineWrite<{ endpoint: WebhookEndpointView }>(
      `/api/webhooks/${encodeURIComponent(id)}`,
      patch,
      "PUT",
      8000,
      actor,
    );
    if (!r.ok || !r.data) return { ok: false, detail: engineDetail(r.data) };
    return { ok: true, data: r.data };
  }

  async function deleteWebhookEndpoint(id: string): Promise<WebhookActionResult> {
    "use server";
    const actor = await webhookActor();
    if (actor === null) return { ok: false, detail: "이 리소스에 접근할 권한이 없습니다." };
    const r = await engineWrite(
      `/api/webhooks/${encodeURIComponent(id)}`,
      {},
      "DELETE",
      8000,
      actor,
    );
    if (!r.ok) return { ok: false, detail: engineDetail(r.data) };
    return { ok: true, data: null };
  }

  async function rotateWebhookEndpoint(
    id: string,
  ): Promise<WebhookActionResult<{ endpoint: WebhookEndpointView }>> {
    "use server";
    const actor = await webhookActor();
    if (actor === null) return { ok: false, detail: "이 리소스에 접근할 권한이 없습니다." };
    const r = await engineWrite<{ endpoint: WebhookEndpointView }>(
      `/api/webhooks/${encodeURIComponent(id)}/rotate`,
      {},
      "POST",
      8000,
      actor,
    );
    if (!r.ok || !r.data) return { ok: false, detail: engineDetail(r.data) };
    return { ok: true, data: r.data };
  }

  async function testWebhookEndpoint(
    id: string,
  ): Promise<WebhookActionResult<{ ok: boolean; endpoint_id: string }>> {
    "use server";
    const actor = await webhookActor();
    if (actor === null) return { ok: false, detail: "이 리소스에 접근할 권한이 없습니다." };
    const r = await engineWrite<{ ok: boolean; endpoint_id: string }>(
      `/api/webhooks/${encodeURIComponent(id)}/test`,
      {},
      "POST",
      15000,
      actor,
    );
    if (!r.ok || !r.data) return { ok: false, detail: engineDetail(r.data) };
    return { ok: true, data: r.data };
  }

  async function saveWebhookChannels(
    channels: Record<string, string[]>,
    confirm: boolean,
  ): Promise<WebhookActionResult<{ event_count: number; channels: Record<string, string[]> }>> {
    "use server";
    const actor = await webhookActor();
    if (actor === null) return { ok: false, detail: "이 리소스에 접근할 권한이 없습니다." };
    const r = await engineWrite<{ event_count: number; channels: Record<string, string[]> }>(
      "/api/webhooks/channels",
      { channels, confirm },
      "PUT",
      8000,
      actor,
    );
    if (!r.ok || !r.data) return { ok: false, detail: engineDetail(r.data) };
    return { ok: true, data: r.data };
  }

  const webhookActions: WebhookSettingsActions = {
    createEndpoint: createWebhookEndpoint,
    updateEndpoint: updateWebhookEndpoint,
    deleteEndpoint: deleteWebhookEndpoint,
    rotateEndpoint: rotateWebhookEndpoint,
    testEndpoint: testWebhookEndpoint,
    saveChannels: saveWebhookChannels,
  };
  const webhookActiveCount = (webhooks?.endpoints ?? []).filter((e) => e.enabled).length;

  return (
    <div className="space-y-4">
      <AccessProtectionSettings initialMode={authStatus.mode} />

      {isAdmin && <UserManagement initialUsers={managedUsers} />}

      {connected && prefs && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">알림 설정</CardTitle>
          </CardHeader>
          <CardContent>
            <AlertSettings initial={prefs} events={events} />
          </CardContent>
        </Card>
      )}

      {connected && delivery?.available && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">알림 전달 상태</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant={delivery.email_configured ? "success" : "warning"}>
                {delivery.email_configured ? "이메일 구성됨" : "이메일 미구성"}
              </Badge>
              <span>{delivery.email_configured ? `${delivery.recipient_count ?? 0}명에게 전달합니다.` : "알림을 받으려면 이메일 설정을 완료하세요."}</span>
            </div>
            {delivery.outbox && (
              <dl className="grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-5">
                <div><dt className="text-muted-foreground">대기</dt><dd className="font-medium tabular-nums">{delivery.outbox.pending}</dd></div>
                <div><dt className="text-muted-foreground">전송 중</dt><dd className="font-medium tabular-nums">{delivery.outbox.inflight}</dd></div>
                <div><dt className="text-muted-foreground">전달됨</dt><dd className="font-medium tabular-nums">{delivery.outbox.delivered}</dd></div>
                <div><dt className="text-muted-foreground">실패 보관</dt><dd><Badge variant={delivery.outbox.dead_letter ? "destructive" : "outline"}>{delivery.outbox.dead_letter}</Badge></dd></div>
                <div><dt className="text-muted-foreground">Outbox</dt><dd><Badge variant={delivery.outbox.running ? "success" : "warning"}>{delivery.outbox.running ? "실행 중" : "중지"}</Badge></dd></div>
              </dl>
            )}
            {webhooks && (
              <div className="flex flex-wrap items-center gap-2 border-t pt-2">
                <Badge variant={webhookActiveCount > 0 ? "success" : "outline"}>
                  Webhook {webhookActiveCount}/{webhooks.count} 활성
                </Badge>
                <span>대기·전송 중·전달됨·실패 보관 수치는 이메일+Webhook 합산입니다.</span>
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {connected && webhooks && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Webhook 알림</CardTitle>
          </CardHeader>
          <CardContent>
            <WebhookSettings
              initialEndpoints={webhooks.endpoints}
              // I2: 조회 실패(null)를 {}로 폴백하지 않는다 — 컴포넌트가 에러+차단 처리.
              initialChannels={webhookChannels?.channels ?? null}
              events={events}
              actions={webhookActions}
            />
          </CardContent>
        </Card>
      )}

      {!connected ? (
        <Card>
          <CardContent className="py-10 text-center text-sm text-muted-foreground">
            엔진 API에 연결되지 않았습니다.
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-4 lg:grid-cols-3">
          {Object.entries(cfg).map(([section, data]) => (
            <Section key={section} title={section} data={(data ?? {}) as Record<string, unknown>} />
          ))}
        </div>
      )}
    </div>
  );
}
