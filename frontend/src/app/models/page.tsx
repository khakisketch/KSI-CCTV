import { ModelActions } from "@/components/model-actions";
import { ModelUpload } from "@/components/model-upload";
import { PromptManager } from "@/components/prompt-manager";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { getModels } from "@/lib/engine";

export const dynamic = "force-dynamic";

export default async function ModelsPage() {
  const res = await getModels();
  const connected = res !== null;
  const models = res?.models ?? [];

  return (
    <div className="space-y-4">
      {connected && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base">모델 업로드</CardTitle>
          </CardHeader>
          <CardContent>
            <ModelUpload />
          </CardContent>
        </Card>
      )}

      {models.length === 0 ? (
        <Card>
          <CardContent className="py-10 text-center text-sm text-muted-foreground">
            {connected ? "등록된 모델이 없습니다." : "엔진 API에 연결되지 않았습니다."}
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {models.map((m) => (
            <Card key={m.name}>
              <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                <CardTitle className="text-base">{m.name}</CardTitle>
                <div className="flex gap-1">
                  <Badge variant={m.enabled ? "default" : "outline"}>{m.enabled ? "설정 활성" : "설정 비활성"}</Badge>
                  <Badge variant={m.runtime?.loaded ? "default" : m.runtime?.error ? "destructive" : "outline"}>
                    {!m.runtime?.available ? "런타임 미확인" : m.runtime.error ? "실행 오류" : m.runtime.loaded ? "실행 중" : "로드 안 됨"}
                  </Badge>
                </div>
              </CardHeader>
              <CardContent className="space-y-2">
                <div className="flex flex-wrap gap-1.5">
                  {m.type && <Badge variant="secondary">{m.type}</Badge>}
                  {m.source && <Badge variant="outline">{m.source}</Badge>}
                </div>
                {m.description && (
                  <p className="text-xs text-muted-foreground" style={{ wordBreak: "keep-all" }}>
                    {m.description}
                  </p>
                )}
                {m.runtime?.available && (
                  <div className="rounded-md border bg-muted/30 p-2 text-xs text-muted-foreground">
                    {m.runtime.error ? (
                      <p>실행 오류: {m.runtime.error}</p>
                    ) : m.runtime.loaded ? (
                      <p>추론 {m.runtime.inference_count ?? 0}회 · 오류 {m.runtime.error_count ?? 0}회{typeof m.runtime.last_latency_ms === "number" ? ` · 최근 ${m.runtime.last_latency_ms.toFixed(1)}ms` : ""}</p>
                    ) : (
                      <p>설정은 저장됐지만 현재 분석 Pipeline에 로드되지 않았습니다.</p>
                    )}
                  </div>
                )}
                {m.type === "open_vocab" ? (
                  <div className="border-t pt-3">
                    <p className="mb-2 text-xs font-medium">탐지 프롬프트</p>
                    <PromptManager name={m.name} current={m.classes ?? []} enabled={m.enabled} />
                  </div>
                ) : (
                  <ModelActions name={m.name} enabled={m.enabled} deletable={m.source === "uploaded"} />
                )}
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
