import { CommissioningDashboard } from "@/components/commissioning-dashboard";
import { getCommissioning } from "@/lib/engine";

export const dynamic = "force-dynamic";

export default async function CommissioningPage() {
  const data = await getCommissioning();
  if (!data) return <p className="p-8 text-sm text-muted-foreground">엔진 연결을 확인한 뒤 다시 시도하세요.</p>;
  return (
    <div className="mx-auto max-w-4xl space-y-4">
      <CommissioningDashboard initial={data} />
    </div>
  );
}
