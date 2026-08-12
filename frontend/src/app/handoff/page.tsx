import { HandoffReport } from "@/components/handoff-report";
import { getCommissioningReport } from "@/lib/engine";

export const dynamic = "force-dynamic";

export default async function HandoffPage() {
  const report = await getCommissioningReport();
  return (
    <div className="mx-auto max-w-4xl space-y-4">
      {report ? <HandoffReport report={report} /> : <p className="text-sm text-muted-foreground">보고서를 불러오지 못했습니다.</p>}
    </div>
  );
}
