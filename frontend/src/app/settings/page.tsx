import { EngineStatus } from "@/components/engine-status";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { getConfig } from "@/lib/engine";

export const dynamic = "force-dynamic";

function Section({ title, data }: { title: string; data: Record<string, unknown> }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <dl className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          {Object.entries(data).map(([k, v]) => (
            <div key={k} className="flex items-center justify-between gap-4 border-b py-1.5 text-sm">
              <dt className="text-muted-foreground">{k}</dt>
              <dd className="font-medium tabular-nums" style={{ wordBreak: "keep-all" }}>
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
  const cfg = await getConfig();
  const connected = cfg !== null;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-2xl font-semibold tracking-tight">설정</h1>
        <EngineStatus connected={connected} />
      </div>
      <p className="text-sm text-muted-foreground" style={{ wordBreak: "keep-all" }}>
        엔진 운영 설정(읽기 전용 — 편집은 다음 단계). 시크릿은 마스킹되어 표시됩니다.
      </p>

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
