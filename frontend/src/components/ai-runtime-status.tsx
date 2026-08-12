import { Badge } from "@/components/ui/badge";
import type { AiRuntimeStatus } from "@/lib/engine";

function behaviorStatus(behavior: AiRuntimeStatus["behavior"]) {
  if (!behavior?.enabled) return { label: "행동 분석: 비활성", variant: "secondary" as const };
  const errors = (behavior.runtime_errors?.pose ?? 0) + (behavior.runtime_errors?.action ?? 0);
  if (!behavior.available || errors >= 3) return { label: !behavior.available ? "행동 분석: 모델 사용 불가" : `행동 분석: 반복 오류 ${errors}건`, variant: "destructive" as const };
  return { label: "행동 분석: 활성", variant: "success" as const };
}

function vlmStatus(vlm: AiRuntimeStatus["vlm"]) {
  if (!vlm?.configured_enabled) return { label: "VLM 검증: 비활성", variant: "secondary" as const };
  if (vlm.queue?.saturated) return { label: "VLM 큐 포화", variant: "destructive" as const };
  if (!vlm.provider_available || !vlm.enabled) return { label: "VLM 검증: 사용 불가", variant: "destructive" as const };
  if (!vlm.runtime_qualified) return { label: "VLM 검증: 미검증", variant: "warning" as const };
  return { label: "VLM 검증: 준비됨", variant: "success" as const };
}

export function AiRuntimeStatus({ runtime }: { runtime?: AiRuntimeStatus | null }) {
  if (!runtime?.behavior && !runtime?.vlm) return null;
  const behavior = behaviorStatus(runtime.behavior);
  const vlm = vlmStatus(runtime.vlm);
  const queue = runtime.vlm?.queue;
  const unverified = runtime.vlm?.lifecycle?.unverified ?? 0;
  return (
    <section className="border-t pt-3" aria-label="AI 런타임 상태">
      <p className="text-sm font-medium">AI 런타임</p>
      <div className="mt-2 flex flex-wrap gap-2 text-xs">
        <Badge variant={behavior.variant}>{behavior.label}</Badge>
        <Badge variant={vlm.variant}>{vlm.label}</Badge>
        {queue && <span className="rounded-md border px-2 py-1">큐 {queue.in_flight ?? 0}/{queue.hard_limit ?? "-"}, 대기 {queue.pending ?? 0}, 누락 {queue.dropped ?? 0}</span>}
        {runtime.vlm?.configured_enabled && <span className="rounded-md border px-2 py-1">검증 불가 {unverified}건</span>}
      </div>
    </section>
  );
}
