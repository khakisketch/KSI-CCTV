"use client";

import { useState } from "react";
import { LoaderCircle, MapPinned } from "lucide-react";
import Image from "next/image";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { CameraPolygonPurpose, CameraZone, CameraZonePoint } from "@/lib/cameras";

export function CameraZoneEditor({ cameraId, initialZones = [], initialRevision }: { cameraId: string; initialZones?: CameraZone[]; initialRevision?: number }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [points, setPoints] = useState<CameraZonePoint[]>([]);
  const [zoneType, setZoneType] = useState<"polygon" | "line">("polygon");
  const [polygonPurpose, setPolygonPurpose] = useState<CameraPolygonPurpose>("restricted");
  const [direction, setDirection] = useState<"any" | "positive" | "negative">("any");
  const [savedZonesOverride, setSavedZones] = useState<CameraZone[] | null>(null);
  const [revisionOverride, setRevision] = useState<number>();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [conflict, setConflict] = useState(false);
  const [draftX, setDraftX] = useState("0.5");
  const [draftY, setDraftY] = useState("0.5");
  const [snapshotAttempt, setSnapshotAttempt] = useState(0);
  const [snapshotState, setSnapshotState] = useState<"loading" | "ready" | "error">("loading");
  const [snapshotTime, setSnapshotTime] = useState<string | null>(null);
  const savedZones = savedZonesOverride ?? initialZones;
  const revision = revisionOverride ?? initialRevision;

  function addPoint(event: React.MouseEvent<HTMLDivElement>) {
    const rect = event.currentTarget.getBoundingClientRect();
    setPoints((current) => [...(zoneType === "line" && current.length >= 2 ? [] : current), {
      x: Number(((event.clientX - rect.left) / rect.width).toFixed(4)),
      y: Number(((event.clientY - rect.top) / rect.height).toFixed(4)),
    }]);
  }

  function addCoordinatePoint() {
    const x = Number(draftX);
    const y = Number(draftY);
    if (!Number.isFinite(x) || !Number.isFinite(y) || x < 0 || x > 1 || y < 0 || y > 1) {
      setMessage("X와 Y는 0부터 1 사이여야 합니다.");
      return;
    }
    setMessage("");
    setPoints((current) => [...(zoneType === "line" && current.length >= 2 ? [] : current), { x, y }]);
  }

  function zoneLabel(zone: CameraZone): string {
    if (zone.type === "line") return "통과 감지선";
    return zone.id === "loitering" ? "배회 감지 구역" : "제한 구역";
  }

  function editZone(zone: CameraZone) {
    setZoneType(zone.type);
    setPoints(zone.points);
    if (zone.type === "line") setDirection(zone.direction ?? "any");
    else setPolygonPurpose(zone.id === "loitering" ? "loitering" : "restricted");
    setMessage(`${zoneLabel(zone)} 설정을 불러왔습니다.`);
  }

  async function persistZones(zones: CameraZone[], successMessage: string) {
    setBusy(true);
    setMessage("");
    setConflict(false);
    try {
      const response = await fetch(`/api/cameras/${encodeURIComponent(cameraId)}/zones`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ zones, expected_revision: revision }),
      });
      const data = (await response.json().catch(() => ({}))) as { revision?: number; detail?: string };
      if (response.status === 409) {
        setConflict(true);
        throw new Error("다른 작업에서 카메라 설정이 변경되었습니다. 최신 설정을 불러온 뒤 다시 시도하세요.");
      }
      if (!response.ok) throw new Error(data.detail ?? "영역을 저장하지 못했습니다.");
      setSavedZones(zones);
      if (typeof data.revision === "number") setRevision(data.revision);
      router.refresh();
      setMessage(successMessage);
      return true;
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "영역 저장 중 오류가 발생했습니다.");
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    const requiredPoints = zoneType === "line" ? 2 : 3;
    if (points.length < requiredPoints) return;
    const zone: CameraZone = { id: zoneType === "line" ? "line" : polygonPurpose, type: zoneType, points };
    if (zoneType === "line") zone.direction = direction;
    const zones = [...savedZones.filter((saved) => saved.id !== zone.id), zone];
    await persistZones(zones, zoneType === "line" ? "통과 감지선을 저장했습니다." : "선택 영역을 저장했습니다.");
  }

  async function removeZone(zone: CameraZone) {
    const removed = await persistZones(
      savedZones.filter((saved) => saved.id !== zone.id),
      `${zoneLabel(zone)}을 삭제했습니다.`,
    );
    if (removed && (zone.type === "line" ? zoneType === "line" : zoneType === "polygon" && polygonPurpose === zone.id)) {
      setPoints([]);
    }
  }

  if (!open) {
    return <Button className="min-h-11" variant="outline" onClick={() => setOpen(true)}><MapPinned /> 영역 편집</Button>;
  }

  const snapshotPath = `/api/cameras/${encodeURIComponent(cameraId)}/snapshot?t=${snapshotAttempt}`;
  return <div className="space-y-2 border-t pt-3">
    <div className="flex flex-wrap items-center justify-between gap-2"><div className="flex gap-2"><Button className="min-h-11" size="sm" variant={zoneType === "polygon" ? "default" : "outline"} onClick={() => { setZoneType("polygon"); setPoints([]); }}>영역</Button><Button className="min-h-11" size="sm" variant={zoneType === "line" ? "default" : "outline"} onClick={() => { setZoneType("line"); setPoints([]); }}>통과 감지선</Button></div><Button className="min-h-11" size="sm" variant="ghost" onClick={() => { setOpen(false); setPoints([]); setMessage(""); }}>편집 닫기</Button></div>
    {savedZones.length > 0 && <div className="space-y-2 rounded-lg border p-2"><p className="text-xs font-medium">저장된 영역</p>{savedZones.map((zone) => <div key={zone.id} className="flex flex-wrap items-center justify-between gap-2 rounded-md bg-muted/50 px-2 py-1.5 text-xs"><span>{zoneLabel(zone)} · {zone.points.length}개 점{zone.type === "line" ? ` · ${zone.direction ?? "any"}` : ""}</span><div className="flex gap-1"><Button size="sm" variant="ghost" disabled={busy} onClick={() => editZone(zone)}>{zoneLabel(zone)} 수정</Button><Button size="sm" variant="ghost" className="text-destructive" disabled={busy} onClick={() => removeZone(zone)}>{zoneLabel(zone)} 삭제</Button></div></div>)}</div>}
    {zoneType === "polygon" && <label className="block space-y-1 text-xs font-medium">영역 용도<select className="min-h-11 w-full rounded-lg border bg-background px-3" value={polygonPurpose} onChange={(event) => setPolygonPurpose(event.target.value as CameraPolygonPurpose)}><option value="restricted">제한 구역</option><option value="loitering">배회 감지 구역</option></select></label>}
    {zoneType === "line" && <label className="block space-y-1 text-xs font-medium">허용 통과 방향<select className="min-h-11 w-full rounded-lg border bg-background px-3" value={direction} onChange={(event) => setDirection(event.target.value as "any" | "positive" | "negative")}><option value="any">양방향</option><option value="positive">화살표가 가리키는 방향만</option><option value="negative">반대 화살표 방향만</option></select></label>}
    <p className="text-xs text-muted-foreground">{zoneType === "line" ? "현재 프레임에서 두 점을 선택해 통과 감지선을 만드세요." : "현재 프레임을 클릭해 다각형 영역을 만드세요."} {snapshotTime && `화면 확인 ${snapshotTime}`}</p>
    <div role="button" tabIndex={0} aria-label="카메라 스냅샷 영역 편집" onClick={addPoint} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); addCoordinatePoint(); } }} className="relative aspect-video cursor-crosshair overflow-hidden rounded-lg border bg-muted">
      <Image unoptimized fill sizes="100vw" src={snapshotPath} alt="카메라 스냅샷" className="object-cover" onLoad={() => { setSnapshotState("ready"); setSnapshotTime(new Date().toLocaleTimeString("ko-KR")); }} onError={() => setSnapshotState("error")} />
      {snapshotState === "loading" && <div className="absolute inset-0 grid place-items-center bg-muted/80 text-xs text-muted-foreground">현재 화면을 불러오는 중입니다.</div>}
      {snapshotState === "error" && <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-muted/95 px-4 text-center text-xs text-muted-foreground"><span role="alert">현재 화면을 불러오지 못했습니다. 카메라가 활성 상태인지 확인하세요.</span><Button size="sm" variant="outline" onClick={(event) => { event.stopPropagation(); setSnapshotState("loading"); setSnapshotAttempt((value) => value + 1); }}>화면 다시 불러오기</Button></div>}
      <svg className="pointer-events-none absolute inset-0 size-full" viewBox="0 0 1 1" preserveAspectRatio="none" aria-hidden="true">
        <defs><marker id="direction-arrow" markerWidth="0.05" markerHeight="0.05" refX="0.04" refY="0.025" orient="auto"><path d="M 0 0 L 0.05 0.025 L 0 0.05 z" fill="currentColor" /></marker></defs>
        {savedZones.filter((zone) => points.length === 0 || zone.id !== (zoneType === "line" ? "line" : polygonPurpose)).map((zone) => zone.type === "line" ? <line key={zone.id} data-testid={`saved-zone-${zone.id}`} x1={zone.points[0]?.x} y1={zone.points[0]?.y} x2={zone.points[1]?.x} y2={zone.points[1]?.y} stroke="currentColor" strokeWidth="0.008" strokeDasharray="0.02 0.012" opacity="0.65" /> : <polygon key={zone.id} data-testid={`saved-zone-${zone.id}`} points={zone.points.map((point) => `${point.x},${point.y}`).join(" ")} fill="currentColor" fillOpacity="0.08" stroke="currentColor" strokeWidth="0.006" strokeDasharray="0.02 0.012" opacity="0.65" />)}
        {points.length > 1 && zoneType === "polygon" && <polygon points={points.map((point) => `${point.x},${point.y}`).join(" ")} fill="currentColor" fillOpacity="0.15" stroke="currentColor" strokeWidth="0.008" />}
        {points.length > 1 && zoneType === "line" && <>
          <line x1={points[0].x} y1={points[0].y} x2={points[1].x} y2={points[1].y} stroke="currentColor" strokeWidth="0.008" />
          {(() => {
            const [start, end] = points;
            const length = Math.hypot(end.x - start.x, end.y - start.y);
            if (!length) return null;
            const sign = direction === "negative" ? -1 : 1;
            const midX = (start.x + end.x) / 2;
            const midY = (start.y + end.y) / 2;
            const offsetX = (-((end.y - start.y) / length)) * 0.12 * sign;
            const offsetY = ((end.x - start.x) / length) * 0.12 * sign;
            return direction === "any" ? <line x1={midX - offsetX} y1={midY - offsetY} x2={midX + offsetX} y2={midY + offsetY} stroke="currentColor" strokeWidth="0.008" markerStart="url(#direction-arrow)" markerEnd="url(#direction-arrow)" /> : <line data-testid="line-direction-arrow" x1={midX} y1={midY} x2={midX + offsetX} y2={midY + offsetY} stroke="currentColor" strokeWidth="0.008" markerEnd="url(#direction-arrow)" />;
          })()}
        </>}
        {points.map((point, index) => <circle key={index} cx={point.x} cy={point.y} r="0.015" fill="currentColor" />)}
      </svg>
    </div>
    <fieldset className="grid grid-cols-[1fr_1fr_auto] items-end gap-2 rounded-lg border p-2">
      <legend className="px-1 text-xs text-muted-foreground">키보드 좌표 입력 (0-1)</legend>
      <label className="space-y-1 text-xs">X<Input aria-label="영역 점 X 좌표" min="0" max="1" step="0.01" type="number" value={draftX} onChange={(event) => setDraftX(event.target.value)} /></label>
      <label className="space-y-1 text-xs">Y<Input aria-label="영역 점 Y 좌표" min="0" max="1" step="0.01" type="number" value={draftY} onChange={(event) => setDraftY(event.target.value)} /></label>
      <Button className="min-h-11" type="button" variant="outline" onClick={addCoordinatePoint}>점 추가</Button>
    </fieldset>
    <div className="flex gap-2"><Button className="min-h-11" variant="ghost" disabled={busy || points.length === 0} onClick={() => setPoints([])}>지우기</Button><Button className="min-h-11" disabled={busy || points.length < (zoneType === "line" ? 2 : 3)} onClick={save}>{busy && <LoaderCircle className="animate-spin" />} {zoneType === "line" ? "감지선 저장" : "영역 저장"}</Button></div>
    {message && <div className="flex flex-wrap items-center gap-2"><p role={conflict ? "alert" : "status"} className="text-xs">{message}</p>{conflict && <Button size="sm" variant="outline" onClick={() => window.location.reload()}>최신 설정 불러오기</Button>}</div>}
  </div>;
}
