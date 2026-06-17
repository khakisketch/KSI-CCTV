"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";

// 모델 카드의 활성/비활성 토글 + (업로드 모델) 삭제. 쓰기는 서버 라우트 경유(키 미노출).
// open_vocab 모델은 PromptManager가 활성화를 담당하므로 이 컴포넌트의 토글은 그 외 모델에만 쓴다.
export function ModelActions({
  name,
  enabled,
  deletable,
}: {
  name: string;
  enabled: boolean;
  deletable: boolean;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [, startTransition] = useTransition();

  async function toggle() {
    setBusy(true);
    try {
      const res = await fetch(`/api/models/${encodeURIComponent(name)}/enabled`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: !enabled }),
      });
      const data = (await res.json().catch(() => ({}))) as { detail?: string };
      if (res.ok) {
        toast.success(!enabled ? "활성화됨 (재시작 후 반영)" : "비활성화됨");
        startTransition(() => router.refresh());
      } else {
        toast.error(data.detail ?? "변경 실패");
      }
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    try {
      const res = await fetch(`/api/models/${encodeURIComponent(name)}`, { method: "DELETE" });
      const data = (await res.json().catch(() => ({}))) as { detail?: string };
      if (res.ok) {
        toast.success("모델이 삭제되었습니다.");
        startTransition(() => router.refresh());
      } else {
        toast.error(data.detail ?? "삭제 실패");
        setConfirming(false);
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-2 border-t pt-3">
      <Button size="sm" variant={enabled ? "outline" : "default"} disabled={busy} onClick={toggle}>
        {enabled ? "비활성화" : "활성화"}
      </Button>
      {deletable &&
        (confirming ? (
          <>
            <Button size="sm" variant="destructive" disabled={busy} onClick={remove}>
              삭제 확인
            </Button>
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => setConfirming(false)}>
              취소
            </Button>
          </>
        ) : (
          <Button size="sm" variant="ghost" disabled={busy} onClick={() => setConfirming(true)}>
            삭제
          </Button>
        ))}
    </div>
  );
}
