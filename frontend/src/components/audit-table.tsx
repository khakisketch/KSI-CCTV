"use client";

import { useMemo, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { AuditEntry } from "@/lib/engine";
import { formatTs } from "@/lib/utils";

const BASE_FIELDS = new Set(["timestamp", "event", "source"]);

function detailFields(e: AuditEntry): Array<[string, unknown]> {
  return Object.entries(e).filter(([k]) => !BASE_FIELDS.has(k));
}

function detailOf(e: AuditEntry): string {
  return detailFields(e)
    .map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : String(v)}`)
    .join(", ");
}

export function AuditTable({ entries, connected }: { entries: AuditEntry[]; connected: boolean }) {
  const [selectedIndex, setSelectedIndex] = useState<number>(0);
  const selected = entries[selectedIndex] ?? null;

  return (
    <div data-workspace className="flex h-full flex-col">
      <div className="flex min-h-0 flex-1">
        <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
          {entries.length === 0 ? (
            <div className="flex flex-1 items-center justify-center p-8">
              <p className="text-sm text-muted-foreground">
                {connected ? "감사 기록이 없습니다." : "엔진 API에 연결되지 않았습니다."}
              </p>
            </div>
          ) : (
            <div className="flex-1 overflow-auto">
              <table className="w-full text-sm">
                <thead className="sticky top-0 z-10 bg-muted/50 text-left text-xs text-muted-foreground">
                  <tr>
                    <th className="whitespace-nowrap px-3 py-2.5 font-medium">시각</th>
                    <th className="whitespace-nowrap px-3 py-2.5 font-medium">이벤트</th>
                    <th className="whitespace-nowrap px-3 py-2.5 font-medium">소스</th>
                    <th className="px-3 py-2.5 font-medium">상세</th>
                  </tr>
                </thead>
                <tbody>
                  {entries.map((e, i) => {
                    const isSelected = i === selectedIndex;
                    return (
                      <tr
                        key={`${e.timestamp ?? ""}-${e.event ?? ""}-${i}`}
                        onClick={() => setSelectedIndex(i)}
                        onKeyDown={(ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); setSelectedIndex(i); } }}
                        tabIndex={0}
                        role="row"
                        aria-selected={isSelected}
                        className={cn(
                          "cursor-pointer border-t transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
                          isSelected ? "bg-accent/40" : "hover:bg-muted/30",
                        )}
                      >
                        <td className="whitespace-nowrap px-3 py-2.5 text-xs text-muted-foreground">{formatTs(e.timestamp)}</td>
                        <td className="px-3 py-2.5"><Badge variant="secondary">{e.event ?? "—"}</Badge></td>
                        <td className="px-3 py-2.5 text-xs text-muted-foreground">{e.source ?? "—"}</td>
                        <td className="max-w-xs break-all px-3 py-2.5 text-xs text-muted-foreground lg:truncate">{detailOf(e) || "—"}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="hidden w-[360px] shrink-0 flex-col border-l lg:flex">
          {selected ? (
            <AuditInspector entry={selected} />
          ) : (
            <div className="flex flex-1 items-center justify-center p-4">
              <p className="text-sm text-muted-foreground">로그를 선택하세요.</p>
            </div>
          )}
        </div>
      </div>
      {selected && (
        <div className="border-t lg:hidden">
          <AuditInspector entry={selected} />
        </div>
      )}
    </div>
  );
}

function AuditInspector({ entry: e }: { entry: AuditEntry }) {
  const fields = useMemo(() => detailFields(e), [e]);

  return (
    <div className="flex min-h-0 flex-col overflow-hidden lg:h-full">
      <div className="shrink-0 border-b px-4 py-3">
        <Badge variant="secondary">{e.event ?? "—"}</Badge>
        <h3 className="mt-2 text-base font-semibold">{e.event ?? "감사 로그"}</h3>
        <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
          <span>{formatTs(e.timestamp)}</span>
          {e.source && <span>소스: {e.source}</span>}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-3">
        {fields.length === 0 ? (
          <p className="text-sm text-muted-foreground">추가 상세 정보 없음</p>
        ) : (
          <dl className="space-y-2">
            {fields.map(([k, v]) => (
              <div key={k} className="border-b pb-2">
                <dt className="text-xs font-medium text-muted-foreground">{k}</dt>
                <dd className="mt-0.5 break-all text-sm tabular-nums" style={{ wordBreak: "break-all" }}>
                  {v === null || v === undefined ? "—" : typeof v === "object" ? <pre className="whitespace-pre-wrap text-xs">{JSON.stringify(v, null, 2)}</pre> : String(v)}
                </dd>
              </div>
            ))}
          </dl>
        )}
      </div>
    </div>
  );
}
