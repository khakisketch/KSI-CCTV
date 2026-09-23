import Link from "next/link";

import { ReportViewer } from "@/components/report-viewer";
import { Card, CardContent } from "@/components/ui/card";
import { getReport, getReports, type ReportPeriod } from "@/lib/engine";

export const dynamic = "force-dynamic";

function parsePeriod(value: string | undefined): ReportPeriod | null {
  if (value === "daily" || value === "weekly") return value;
  return null;
}

export default async function ReportsPage({
  searchParams,
}: {
  searchParams: Promise<{ id?: string; period?: string }>;
}) {
  const params = await searchParams;
  const period = parsePeriod(params.period);
  const list = await getReports(period ? { period } : {});

  if (!list) {
    return (
      <div className="flex h-full items-center justify-center p-8">
        <Card>
          <CardContent className="py-12 text-center text-sm text-muted-foreground">
            엔진 API에 연결되지 않았습니다.
          </CardContent>
        </Card>
      </div>
    );
  }

  const selectedId = Number(params.id);
  const validId = Number.isInteger(selectedId) && selectedId > 0 ? selectedId : null;
  const detail = validId !== null ? await getReport(validId) : null;

  return (
    <div className="space-y-4">
      {validId !== null && !detail && (
        <Card>
          <CardContent className="py-4 text-center text-sm text-muted-foreground">
            보고서를 찾을 수 없습니다.{" "}
            <Link className="text-primary underline-offset-4 hover:underline" href="/reports">
              목록으로 돌아가기
            </Link>
          </CardContent>
        </Card>
      )}
      <ReportViewer
        list={period ? { ...list, reports: list.reports.filter((r) => r.period === period) } : list}
        detail={detail}
        selectedId={validId}
        activePeriod={period}
      />
    </div>
  );
}
