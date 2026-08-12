import { DiagnosticsPanel } from "@/components/diagnostics-panel";
import { getCommissioning, getPreflight, getSystem } from "@/lib/engine";

export const dynamic = "force-dynamic";

export default async function DiagnosticsPage() {
  const [preflight, commissioning, system] = await Promise.all([getPreflight(), getCommissioning(), getSystem()]);
  const checks = preflight?.checks ?? commissioning?.checks ?? [];
  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <DiagnosticsPanel initial={checks} initialAiRuntime={system?.ai_runtime} />
    </div>
  );
}
