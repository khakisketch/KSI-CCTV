import { Activity, Bell, Camera, Cpu } from "lucide-react";

import { EngineStatus } from "@/components/engine-status";
import { PipelineControl } from "@/components/pipeline-control";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { getCameras, getEvents, getModels } from "@/lib/engine";
import { formatTs } from "@/lib/utils";

export const dynamic = "force-dynamic"; // 요청 시 엔진에서 실데이터 페치(빌드타임 정적생성 회피)

function within24h(ts?: string): boolean {
  if (!ts) return false;
  const t = Date.parse(ts);
  return Number.isFinite(t) && Date.now() - t < 24 * 3600 * 1000;
}

export default async function DashboardPage() {
  const [models, eventsRes, cameras] = await Promise.all([getModels(), getEvents(50), getCameras()]);
  const connected = models !== null || eventsRes !== null || cameras !== null;

  const camerasOn = (cameras?.cameras ?? []).filter((c) => c.enabled).length;
  const activeModels = (models?.models ?? []).filter((m) => m.enabled).length;
  const events = eventsRes?.events ?? [];
  const todayCount = events.filter((e) => within24h(e.timestamp)).length;

  const metrics = [
    { label: "카메라", value: connected ? String(camerasOn) : "—", sub: "활성", icon: Camera },
    { label: "오늘 이벤트", value: connected ? String(todayCount) : "—", sub: "최근 24시간", icon: Bell },
    { label: "활성 모델", value: connected ? String(activeModels) : "—", sub: "탐지 모델", icon: Cpu },
    {
      label: "엔진 상태",
      value: connected ? "정상" : "대기",
      sub: connected ? "API 응답" : "API 미연결",
      icon: Activity,
    },
  ];

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">관제 대시보드</h1>
          <p className="text-sm text-muted-foreground" style={{ wordBreak: "keep-all" }}>
            실시간 안전 이벤트 현황. 학습 없이 텍스트 프롬프트로 객체를 탐지합니다.
          </p>
        </div>
        <EngineStatus connected={connected} />
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {metrics.map(({ label, value, sub, icon: Icon }) => (
          <Card key={label}>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">{label}</CardTitle>
              <Icon className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-3xl font-bold tracking-tight">{value}</div>
              <p className="mt-1 text-xs text-muted-foreground" style={{ wordBreak: "keep-all" }}>
                {sub}
              </p>
            </CardContent>
          </Card>
        ))}
      </div>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
          <CardTitle className="text-base">파이프라인 제어</CardTitle>
          <PipelineControl />
        </CardHeader>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">최근 이벤트</CardTitle>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>시각</TableHead>
                <TableHead>카메라</TableHead>
                <TableHead>유형</TableHead>
                <TableHead className="text-right">신뢰도</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {events.length === 0 ? (
                <TableRow>
                  <TableCell
                    colSpan={4}
                    className="h-28 text-center text-sm text-muted-foreground"
                    style={{ wordBreak: "keep-all" }}
                  >
                    {connected
                      ? "아직 이벤트가 없습니다."
                      : "엔진을 연결하면 실시간 이벤트가 여기에 표시됩니다."}
                  </TableCell>
                </TableRow>
              ) : (
                events.slice(0, 20).map((e, i) => (
                  <TableRow key={e.event_id ?? i}>
                    <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                      {formatTs(e.timestamp)}
                    </TableCell>
                    <TableCell>{e.camera_id ?? "—"}</TableCell>
                    <TableCell>
                      <Badge variant="secondary">{e.event_type ?? "—"}</Badge>
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {typeof e.confidence === "number" ? e.confidence.toFixed(2) : "—"}
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}
