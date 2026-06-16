"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";

/** open-vocab 모델 프롬프트 편집기 — 학습 없이 텍스트로 탐지 대상 변경(POST는 서버 라우트 경유, 키 미노출). */
export function PromptManager({
  name,
  current,
  enabled,
}: {
  name: string;
  current: string[];
  enabled: boolean;
}) {
  const router = useRouter();
  const [text, setText] = useState((current ?? []).join("\n"));
  const [enable, setEnable] = useState(enabled);
  const [pending, startTransition] = useTransition();

  function parse(): string[] {
    const seen = new Set<string>();
    const out: string[] = [];
    for (const raw of text.split(/[\n,]/)) {
      const s = raw.trim();
      if (s && !seen.has(s)) {
        seen.add(s);
        out.push(s);
      }
    }
    return out;
  }

  async function save() {
    const classes = parse();
    if (classes.length === 0) {
      toast.error("프롬프트를 한 개 이상 입력하세요.");
      return;
    }
    const res = await fetch(`/api/models/${encodeURIComponent(name)}/prompts`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ classes, enabled: enable }),
    });
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
      toast.success(`프롬프트 ${data.classes?.length ?? classes.length}개 저장됨 (재시작 후 반영)`);
      startTransition(() => router.refresh());
    } else {
      toast.error(data.detail ?? "저장 실패");
    }
  }

  return (
    <div className="space-y-2">
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={4}
        placeholder="탐지할 객체를 줄/쉼표로 구분해 입력 (예: 지게차, 안전모 미착용, 화재)"
        className="w-full resize-y rounded-md border bg-background p-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring"
        style={{ wordBreak: "keep-all" }}
      />
      <div className="flex items-center justify-between gap-2">
        <label className="flex items-center gap-2 text-sm text-muted-foreground">
          <input
            type="checkbox"
            checked={enable}
            onChange={(e) => setEnable(e.target.checked)}
            className="h-4 w-4 accent-primary"
          />
          저장 시 활성화
        </label>
        <Button size="sm" disabled={pending} onClick={save}>
          프롬프트 저장
        </Button>
      </div>
    </div>
  );
}
