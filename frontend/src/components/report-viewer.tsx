"use client";

import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  formatReportPeriod,
  reportDownloadFilename,
  type ReportDetail,
  type ReportPeriod,
  type ReportsList,
} from "@/lib/engine";

function downloadMarkdown(report: ReportDetail) {
  const blob = new Blob([report.markdown], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = reportDownloadFilename(report);
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

function formatDateTime(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("ko-KR", { timeZone: "Asia/Seoul" });
}

const PERIODS: ReportPeriod[] = ["daily", "weekly"];

export function ReportViewer({
  list,
  detail,
  selectedId,
  activePeriod,
}: {
  list: ReportsList;
  detail: ReportDetail | null;
  selectedId: number | null;
  activePeriod: ReportPeriod | null;
}) {
  const status = list.status;
  const periodHref = (period: ReportPeriod | null) =>
    period ? `/reports?period=${period}` : "/reports";

  return (
    <div className="space-y-4">
      {status && (
        <Card>
          <CardHeader>
            <div className="flex flex-wrap items-center gap-2">
              <CardTitle className="text-base">보고서 스케줄 상태</CardTitle>
              <Badge variant={status.enabled ? "success" : "outline"}>
                {status.enabled ? "스케줄 활성화" : "스케줄 비활성"}
              </Badge>
              {!status.live && (
                <Badge variant="outline">스케줄러 미연결(저장소 기준 추정)</Badge>
              )}
            </div>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <p className="text-muted-foreground">보존 기간: {status.retention_days}일</p>
            <dl className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              {PERIODS.map((period) => {
                const state = status.periods[period];
                return (
                  <div key={period} className="rounded-md border p-3">
                    <dt className="flex items-center gap-2 font-medium">
                      {formatReportPeriod(period)} 보고서
                      <Badge variant={state.enabled ? "outline" : "warning"}>
                        {state.enabled ? "주기 켜짐" : "주기 꺼짐"}
                      </Badge>
                      {state.failure_count !== null && state.failure_count > 0 && (
                        <Badge variant="destructive">연속 실패 {state.failure_count}</Badge>
                      )}
                    </dt>
                    <dd className="mt-2 space-y-1 text-muted-foreground">
                      <div>마지막 성공: {formatDateTime(state.last_success)}</div>
                      <div>다음 실행: {formatDateTime(state.next_run)}</div>
                    </dd>
                  </div>
                );
              })}
            </dl>
          </CardContent>
        </Card>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-1">
          <CardHeader>
            <div className="flex flex-wrap items-center gap-2">
              <CardTitle className="text-base">보고서 목록 ({list.count})</CardTitle>
              <Link
                href={periodHref(null)}
                className={`text-xs underline-offset-4 hover:underline ${activePeriod === null ? "font-bold" : "text-muted-foreground"}`}
              >
                전체
              </Link>
              {PERIODS.map((period) => (
                <Link
                  key={period}
                  href={periodHref(period)}
                  className={`text-xs underline-offset-4 hover:underline ${activePeriod === period ? "font-bold" : "text-muted-foreground"}`}
                >
                  {formatReportPeriod(period)}
                </Link>
              ))}
            </div>
          </CardHeader>
          <CardContent>
            {list.reports.length === 0 ? (
              <p className="py-6 text-center text-sm text-muted-foreground">
                표시할 보고서가 없습니다.
              </p>
            ) : (
              <ul className="space-y-2">
                {list.reports.map((item) => {
                  const href = activePeriod
                    ? `/reports?period=${activePeriod}&id=${item.id}`
                    : `/reports?id=${item.id}`;
                  const active = item.id === selectedId;
                  return (
                    <li key={item.id}>
                      <Link
                        href={href}
                        className={`block rounded-md border p-2.5 text-sm hover:bg-muted ${active ? "border-primary" : ""}`}
                      >
                        <div className="flex items-center gap-2">
                          <Badge variant="outline">{formatReportPeriod(item.period)}</Badge>
                          <span className="font-medium">#{item.id}</span>
                        </div>
                        <div className="mt-1 text-xs text-muted-foreground">
                          {formatDateTime(item.created_at)} · {item.model || "모델 미표기"}
                        </div>
                      </Link>
                    </li>
                  );
                })}
              </ul>
            )}
          </CardContent>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader>
            <div className="flex flex-wrap items-center gap-2">
              <CardTitle className="text-base">
                {detail
                  ? `${formatReportPeriod(detail.period)} 보고서 #${detail.id}`
                  : "보고서 열람"}
              </CardTitle>
              {detail && (
                <Button size="sm" variant="outline" onClick={() => downloadMarkdown(detail)}>
                  Markdown 다운로드
                </Button>
              )}
            </div>
          </CardHeader>
          <CardContent>
            {!detail ? (
              <p className="py-10 text-center text-sm text-muted-foreground">
                목록에서 보고서를 선택하세요.
              </p>
            ) : (
              <pre className="max-h-[60vh] overflow-auto whitespace-pre-wrap break-words rounded-md bg-muted p-4 text-sm">
                {detail.markdown}
              </pre>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
