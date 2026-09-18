import { cookies } from "next/headers";

import { AlertSettings } from "@/components/alert-settings";
import { AccessProtectionSettings } from "@/components/access-protection-settings";
import { UserManagement } from "@/components/user-management";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { toPublicUser } from "@/app/api/users/route";
import { getAlertDeliveryStatus, getAlertPreferences, getConfig, getPolicies } from "@/lib/engine";
import { getAuthStatus, SESSION_COOKIE, sessionPrincipal } from "@/lib/auth";
import { listUsers } from "@/lib/users";

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
  const [cfg, prefs, policies, delivery, authStatus] = await Promise.all([
    getConfig(),
    getAlertPreferences(),
    getPolicies(),
    getAlertDeliveryStatus(),
    getAuthStatus(),
  ]);
  const connected = cfg !== null;
  const events = deriveEvents(policies);
  const principal = await sessionPrincipal((await cookies()).get(SESSION_COOKIE)?.value);
  const isAdmin = principal?.role === "admin";
  const managedUsers = isAdmin ? (await listUsers()).map(toPublicUser) : [];

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
