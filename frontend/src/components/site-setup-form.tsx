"use client";

import Link from "next/link";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { CommissioningSite } from "@/lib/engine";

export function SiteSetupForm({ site, revision }: { site: CommissioningSite; revision: number }) {
  const [form, setForm] = useState(site);
  const [currentRevision, setCurrentRevision] = useState(revision);
  const [message, setMessage] = useState("");
  const [saving, setSaving] = useState(false);
  const update = (field: keyof CommissioningSite, value: string | number) => setForm((old) => ({ ...old, [field]: value }));

  async function save() {
    setSaving(true); setMessage("");
    const response = await fetch("/api/commissioning/site", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...form, expected_revision: currentRevision }) });
    const data = await response.json().catch(() => ({}));
    setSaving(false);
    if (!response.ok) { setMessage(response.status === 409 ? "다른 사용자가 정보를 변경했습니다. 새로고침 후 다시 저장하세요." : data.detail ?? "저장하지 못했습니다."); return; }
    if (typeof data.revision === "number") setCurrentRevision(data.revision);
    setMessage("현장 정보를 저장했습니다.");
  }

  return <div className="space-y-4">
    {message && <p role="alert" className="text-sm text-muted-foreground">{message}</p>}
    <div className="grid gap-4 sm:grid-cols-2">
      <label className="grid gap-1.5 text-sm">현장 이름<Input value={form.name} onChange={(e) => update("name", e.target.value)} /></label>
      <label className="grid gap-1.5 text-sm">현장 위치<Input value={form.location} onChange={(e) => update("location", e.target.value)} /></label>
      <label className="grid gap-1.5 text-sm">시간대<Input value={form.timezone} onChange={(e) => update("timezone", e.target.value)} placeholder="Asia/Seoul" /></label>
      <label className="grid gap-1.5 text-sm">설치 담당자<Input value={form.installer} onChange={(e) => update("installer", e.target.value)} /></label>
      <label className="grid gap-1.5 text-sm">예정 카메라 수<Input type="number" min="0" value={form.expected_camera_count} onChange={(e) => update("expected_camera_count", Number(e.target.value))} /></label>
    </div>
    <div className="flex flex-wrap gap-2">
      <Button type="button" className="min-h-11" onClick={save} disabled={saving}>{saving ? "저장 중" : "현장 정보 저장"}</Button>
      <Link href="/commissioning" className="inline-flex min-h-11 items-center justify-center rounded-md border border-input px-4 text-sm font-medium">설치 진행으로 이동</Link>
    </div>
  </div>;
}
