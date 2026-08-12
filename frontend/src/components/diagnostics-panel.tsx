"use client";

import { Fragment, useState } from "react";

import { CommissioningStatus } from "@/components/commissioning-status";
import { AiRuntimeStatus } from "@/components/ai-runtime-status";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { AiRuntimeStatus as AiRuntimeStatusData, CommissioningCheck, ControlStatus } from "@/lib/engine";

export function DiagnosticsPanel({ initial, initialRuntime, initialAiRuntime }: { initial: CommissioningCheck[]; initialRuntime?: ControlStatus | null; initialAiRuntime?: AiRuntimeStatusData | null }) {
  const [checks, setChecks] = useState(initial);
  const [aiRuntime, setAiRuntime] = useState(initialAiRuntime ?? initialRuntime?.status?.ai_runtime);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const ordered = [...checks].sort((left, right) => statusOrder(left.status) - statusOrder(right.status));
  const selectedCheck = ordered.find((c) => c.id === selectedId) ?? ordered[0] ?? null;

  async function refresh() {
    setLoading(true);
    setError("");
    setMessage("");
    try {
      const response = await fetch("/api/preflight", { cache: "no-store" });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        setError(data.detail ?? "진단을 새로 고치지 못했습니다.");
        return;
      }
      setChecks(data.checks ?? []);
      if (data.ai_runtime) setAiRuntime(data.ai_runtime as AiRuntimeStatusData);
      const systemResponse = await fetch("/api/system", { cache: "no-store" });
      if (systemResponse.ok) {
        const system = await systemResponse.json() as { ai_runtime?: AiRuntimeStatusData };
        if (system.ai_runtime) setAiRuntime(system.ai_runtime);
      }
      setMessage("진단 결과를 갱신했습니다.");
    } catch {
      setError("진단을 새로 고치지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div data-workspace className="flex h-full flex-col">
      <div className="flex shrink-0 items-center justify-end gap-2 border-b px-4 py-2.5">
        {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
        {message && !error && <p role="status" className="text-xs text-muted-foreground">{message}</p>}
        <div className="ml-auto flex gap-2">
          <a href="/api/support/bundle" download className="inline-flex min-h-9 items-center rounded-md border border-input px-3 text-xs font-medium hover:bg-muted">지원 자료</a>
          <Button variant="outline" size="sm" onClick={refresh} disabled={loading}>{loading ? "확인 중" : "다시 확인"}</Button>
        </div>
      </div>

      <div className="flex min-h-0 flex-1">
        <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
          {ordered.length === 0 ? (
            <div className="flex flex-1 items-center justify-center p-8">
              <p className="text-sm text-muted-foreground">표시할 진단 결과가 없습니다.</p>
            </div>
          ) : (
            <div className="flex-1 overflow-y-auto">
              <table className="w-full text-sm">
                <thead className="sticky top-0 z-10 bg-muted/50 text-left text-xs text-muted-foreground">
                  <tr>
                    <th className="whitespace-nowrap px-3 py-2.5 font-medium">상태</th>
                    <th className="px-3 py-2.5 font-medium">항목</th>
                    <th className="px-3 py-2.5 font-medium">요약</th>
                  </tr>
                </thead>
                <tbody>
                  {ordered.map((check) => {
                    const isSelected = check.id === selectedCheck?.id;
                    return (
                      <Fragment key={check.id}>
                        <tr
                          onClick={() => setSelectedId(check.id)}
                          onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setSelectedId(check.id); } }}
                          tabIndex={0}
                          role="row"
                          aria-selected={isSelected}
                          className={cn(
                            "cursor-pointer border-t transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
                            isSelected ? "bg-accent/40" : "hover:bg-muted/30",
                          )}
                        >
                          <td className="whitespace-nowrap px-3 py-2.5"><CommissioningStatus status={check.status} /></td>
                          <td className="px-3 py-2.5 font-medium">{check.label}</td>
                          <td className="max-w-xs truncate px-3 py-2.5 text-muted-foreground">{check.summary}</td>
                        </tr>
                        {check.remediation && (
                          <tr className="lg:hidden">
                            <td colSpan={3} className="border-t-0 bg-muted/20 px-3 py-2 text-xs text-muted-foreground">
                              <span className="font-medium">권장 조치: </span>{check.remediation}
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="hidden w-[360px] shrink-0 flex-col border-l lg:flex">
          {selectedCheck ? (
            <div className="flex h-full flex-col overflow-hidden">
              <div className="shrink-0 border-b px-4 py-3">
                <div className="flex items-center gap-2">
                  <CommissioningStatus status={selectedCheck.status} />
                </div>
                <h3 className="mt-2 text-base font-semibold">{selectedCheck.label}</h3>
              </div>
              <div className="flex-1 overflow-y-auto px-4 py-3">
                <p className="text-sm text-muted-foreground" style={{ wordBreak: "keep-all" }}>{selectedCheck.summary}</p>
                {selectedCheck.remediation && (
                  <div className="mt-3 border-t pt-3">
                    <p className="text-sm font-medium">권장 조치</p>
                    <p className="mt-1 text-sm text-muted-foreground" style={{ wordBreak: "keep-all" }}>{selectedCheck.remediation}</p>
                  </div>
                )}
              </div>
            </div>
          ) : (
            <div className="flex flex-1 items-center justify-center p-4">
              <p className="text-sm text-muted-foreground">항목을 선택하세요.</p>
            </div>
          )}
        </div>
      </div>
      <div className="shrink-0 border-t px-4 py-3"><AiRuntimeStatus runtime={aiRuntime} /></div>
    </div>
  );
}

function statusOrder(status: CommissioningCheck["status"]): number {
  return status === "fail" ? 0 : status === "warning" ? 1 : 2;
}
