import { Badge } from "@/components/ui/badge";

/** 엔진 연결 상태 배지 — 데이터 페치 성공 여부로 connected 전달. */
export function EngineStatus({ connected }: { connected: boolean }) {
  return (
    <Badge variant="outline" className="gap-1.5">
      <span
        className={`h-2 w-2 rounded-full ${connected ? "bg-emerald-500" : "bg-amber-500"}`}
      />
      {connected ? "엔진 연결됨" : "엔진 API 미연결"}
    </Badge>
  );
}
