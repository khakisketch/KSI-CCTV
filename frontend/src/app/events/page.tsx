import { EngineStatus } from "@/components/engine-status";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { getEvents } from "@/lib/engine";

export const dynamic = "force-dynamic";

const LEVEL_VARIANT: Record<string, "default" | "secondary" | "destructive" | "outline"> = {
  critical: "destructive",
  high: "destructive",
  medium: "default",
  low: "secondary",
};

export default async function EventsPage() {
  const res = await getEvents(200);
  const connected = res !== null;
  const events = res?.events ?? [];

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-2xl font-semibold tracking-tight">이벤트</h1>
        <EngineStatus connected={connected} />
      </div>
      <Card>
        <CardContent className="pt-6">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>시각</TableHead>
                <TableHead>카메라</TableHead>
                <TableHead>유형</TableHead>
                <TableHead>등급</TableHead>
                <TableHead>설명</TableHead>
                <TableHead className="text-right">신뢰도</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {events.length === 0 ? (
                <TableRow>
                  <TableCell
                    colSpan={6}
                    className="h-28 text-center text-sm text-muted-foreground"
                    style={{ wordBreak: "keep-all" }}
                  >
                    {connected ? "이벤트가 없습니다." : "엔진 API에 연결되지 않았습니다."}
                  </TableCell>
                </TableRow>
              ) : (
                events.map((e, i) => (
                  <TableRow key={e.event_id ?? i}>
                    <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                      {e.timestamp?.replace("T", " ").slice(0, 19) ?? "—"}
                    </TableCell>
                    <TableCell>{e.camera_id ?? "—"}</TableCell>
                    <TableCell>
                      <Badge variant="secondary">{e.event_type ?? "—"}</Badge>
                    </TableCell>
                    <TableCell>
                      {e.alert_level ? (
                        <Badge variant={LEVEL_VARIANT[e.alert_level] ?? "outline"}>
                          {e.alert_level}
                        </Badge>
                      ) : (
                        "—"
                      )}
                    </TableCell>
                    <TableCell
                      className="max-w-[28rem] truncate text-sm text-muted-foreground"
                      style={{ wordBreak: "keep-all" }}
                    >
                      {e.description ?? "—"}
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
