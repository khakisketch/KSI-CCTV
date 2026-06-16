"use client";

import { useRouter } from "next/navigation";
import { useRef, useState, useTransition } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

// 사용자 ONNX 모델 업로드 폼. 멀티파트로 서버 라우트(/api/models/upload)에 보내고, 서버가
// 엔진의 하드닝 검증 샌드박스로 처리한다. 검증 통과 시 "비활성"으로 등록(운영자가 카드에서 활성화).
export function ModelUpload() {
  const router = useRouter();
  const fileRef = useRef<HTMLInputElement>(null);
  const [name, setName] = useState("");
  const [classes, setClasses] = useState("");
  const [height, setHeight] = useState(640);
  const [width, setWidth] = useState(640);
  const [busy, setBusy] = useState(false);
  const [, startTransition] = useTransition();

  async function submit() {
    const file = fileRef.current?.files?.[0];
    if (!file) {
      toast.error("업로드할 ONNX(.onnx) 파일을 선택하세요.");
      return;
    }
    const fd = new FormData();
    fd.append("file", file);
    fd.append("model_name", name.trim() || file.name);
    fd.append("classes", classes);
    fd.append("input_height", String(height));
    fd.append("input_width", String(width));

    setBusy(true);
    try {
      const res = await fetch("/api/models/upload", { method: "POST", body: fd });
      const data = (await res.json().catch(() => ({}))) as {
        profile_name?: string;
        detail?: string;
      };
      if (res.ok) {
        toast.success(`업로드·검증 완료: ${data.profile_name ?? "모델"} (비활성 등록)`);
        if (fileRef.current) fileRef.current.value = "";
        setName("");
        setClasses("");
        startTransition(() => router.refresh());
      } else {
        toast.error(data.detail ?? "업로드 실패");
      }
    } catch {
      toast.error("업로드 중 오류가 발생했습니다.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="space-y-1 text-sm">
          <span className="text-muted-foreground">ONNX 파일</span>
          <Input ref={fileRef} type="file" accept=".onnx" disabled={busy} />
        </label>
        <label className="space-y-1 text-sm">
          <span className="text-muted-foreground">모델 표시 이름</span>
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="예: 현장 안전모 탐지"
            disabled={busy}
          />
        </label>
      </div>
      <label className="block space-y-1 text-sm">
        <span className="text-muted-foreground">클래스 목록 (쉼표 구분, 선택)</span>
        <Input
          value={classes}
          onChange={(e) => setClasses(e.target.value)}
          placeholder="예: 안전모, 안전모 미착용, 사람"
          disabled={busy}
        />
      </label>
      <div className="flex flex-wrap items-end gap-3">
        <label className="space-y-1 text-sm">
          <span className="text-muted-foreground">입력 높이</span>
          <Input
            type="number"
            value={height}
            min={32}
            max={4096}
            step={32}
            onChange={(e) => setHeight(Number(e.target.value))}
            disabled={busy}
            className="w-28"
          />
        </label>
        <label className="space-y-1 text-sm">
          <span className="text-muted-foreground">입력 너비</span>
          <Input
            type="number"
            value={width}
            min={32}
            max={4096}
            step={32}
            onChange={(e) => setWidth(Number(e.target.value))}
            disabled={busy}
            className="w-28"
          />
        </label>
        <Button onClick={submit} disabled={busy} className="ml-auto">
          {busy ? "검증 중…" : "업로드 후 검증"}
        </Button>
      </div>
      <p className="text-xs text-muted-foreground" style={{ wordBreak: "keep-all" }}>
        업로드된 모델은 보안 샌드박스 검증을 거쳐 <strong>비활성</strong>으로 등록됩니다. 활성화는 아래
        모델 카드에서 할 수 있습니다. (최대 500MB, .onnx)
      </p>
    </div>
  );
}
