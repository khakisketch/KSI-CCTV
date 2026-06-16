import { EngineStatus } from "@/components/engine-status";
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
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-2xl font-semibold tracking-tight">모델 · 프롬프트</h1>
        <EngineStatus connected={connected} />
      </div>
      <p className="text-sm text-muted-foreground" style={{ wordBreak: "keep-all" }}>
        등록된 탐지 모델 목록입니다. <code>open_vocab</code> 모델은 학습 없이 텍스트 프롬프트로
        객체를 탐지하며, 사용자 ONNX 모델을 업로드해 추가할 수 있습니다.
      </p>

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
                <Badge variant={m.enabled ? "default" : "outline"}>
                  {m.enabled ? "활성" : "비활성"}
                </Badge>
              </CardHeader>
              <CardContent className="space-y-2">
                <div className="flex flex-wrap gap-1.5">
                  {m.type && <Badge variant="secondary">{m.type}</Badge>}
                  {m.source && <Badge variant="outline">{m.source}</Badge>}
                </div>
                {m.description && (
                  <p
                    className="text-xs text-muted-foreground"
                    style={{ wordBreak: "keep-all" }}
                  >
                    {m.description}
                  </p>
                )}
                {m.type === "open_vocab" && (
                  <div className="border-t pt-3">
                    <p className="mb-2 text-xs font-medium">탐지 프롬프트 (학습 불필요)</p>
                    <PromptManager
                      name={m.name}
                      current={m.classes ?? []}
                      enabled={m.enabled}
                    />
                  </div>
                )}
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
