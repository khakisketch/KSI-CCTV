import { Badge } from "@/components/ui/badge";
import type { CommissioningCheckStatus } from "@/lib/engine";

export function CommissioningStatus({ status }: { status: CommissioningCheckStatus }) {
  const labels = { pass: "통과", warning: "확인 필요", fail: "미통과" };
  return <Badge variant={status === "pass" ? "success" : status === "fail" ? "destructive" : "warning"}>{labels[status]}</Badge>;
}
