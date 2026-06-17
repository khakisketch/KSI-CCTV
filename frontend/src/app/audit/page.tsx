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
import { getAudit, type AuditEntry } from "@/lib/engine";

export const dynamic = "force-dynamic";

const BASE_FIELDS = new Set(["timestamp", "event", "source"]);

function detailOf(e: AuditEntry): string {
  return Object.entries(e)
    .filter(([k]) => !BASE_FIELDS.has(k))
    .map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : String(v)}`)
    .join(", ");
}

export default async function AuditPage() {
  const res = await getAudit(200);
  const connected = res !== null;
  const entries = res?.entries ?? [];

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-2xl font-semibold tracking-tight">감사 로그</h1>
        <EngineStatus connected={connected} />
      </div>
      <p className="text-sm text-muted-foreground" style={{ wordBreak: "keep-all" }}>
        모델 업로드·설정 변경·파이프라인 제어 등 관리 작업 기록입니다(최신순). 민감 정보는 마스킹됩니다.
      </p>
      <Card>
        <CardContent className="pt-6">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>시각</TableHead>
                <TableHead>이벤트</TableHead>
                <TableHead>소스</TableHead>
                <TableHead>상세</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {entries.length === 0 ? (
                <TableRow>
                  <TableCell
                    colSpan={4}
                    className="h-28 text-center text-sm text-muted-foreground"
                    style={{ wordBreak: "keep-all" }}
                  >
                    {connected ? "감사 기록이 없습니다." : "엔진 API에 연결되지 않았습니다."}
                  </TableCell>
                </TableRow>
              ) : (
                entries.map((e, i) => (
                  <TableRow key={i}>
                    <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                      {e.timestamp?.replace("T", " ").slice(0, 19) ?? "—"}
                    </TableCell>
                    <TableCell>
                      <Badge variant="secondary">{e.event ?? "—"}</Badge>
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">{e.source ?? "—"}</TableCell>
                    <TableCell
                      className="max-w-[32rem] truncate text-xs text-muted-foreground"
                      style={{ wordBreak: "keep-all" }}
                    >
                      {detailOf(e) || "—"}
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
