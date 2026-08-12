import { Suspense } from "react";

import { AuditTable } from "@/components/audit-table";
import { getAudit } from "@/lib/engine";

export const dynamic = "force-dynamic";

export default async function AuditPage() {
  const res = await getAudit(200);
  const connected = res !== null;
  const entries = res?.entries ?? [];

  return (
    <Suspense
      fallback={
        <div className="flex h-full items-center justify-center">
          <p className="text-sm text-muted-foreground">감사 로그를 불러오는 중…</p>
        </div>
      }
    >
      <AuditTable entries={entries} connected={connected} />
    </Suspense>
  );
}
