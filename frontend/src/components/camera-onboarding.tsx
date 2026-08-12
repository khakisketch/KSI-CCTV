"use client";

import { useEffect, useState } from "react";
import {
  Check,
  ChevronLeft,
  ChevronRight,
  LoaderCircle,
  MonitorUp,
  Plus,
  RefreshCw,
  Router,
  TriangleAlert,
  Usb,
  Video,
  X,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { redactCameraMessage } from "@/lib/cameras";
import type { EventCatalogItem } from "@/lib/engine";

type PolicyEvent = { name: string; alert_level?: string; enabled?: boolean };
type DiscoveryCamera = {
  name?: string;
  host?: string;
  rtsp_url?: string;
  onvif_url?: string;
  onvif_port?: number;
  discovery_id?: string;
  model?: string;
  source_type?: "onvif" | "gateway";
};
type StreamTest = {
  ok?: boolean;
  connected?: boolean;
  diagnostics?: Record<string, string | number | boolean | null>;
  detail?: string;
};
type ApplyResult = { applied?: boolean; apply_error?: string | null; camera?: { id?: string } };
type OnvifProfile = {
  token?: string;
  name?: string;
  rtsp_url?: string;
  encoding?: string;
  resolution?: [number, number] | null;
  fps?: number | null;
};
type SourceCapability = { kind?: string; available?: boolean; reason?: string };

const STEPS = ["카메라 검색", "자격 증명", "스트림 검사", "감지 설정", "검토 및 적용"];
const REQUEST_TIMEOUT_MS = 15_000;

async function fetchWithTimeout(input: RequestInfo | URL, init: RequestInit): Promise<Response> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    return await fetch(input, { ...init, signal: controller.signal });
  } catch (error) {
    if (controller.signal.aborted) {
      throw new Error("요청 시간이 초과되었습니다. 연결과 VLAN을 확인한 뒤 다시 시도하세요.");
    }
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}

function registrationDefaults(names: string[], catalog: EventCatalogItem[]): string[] {
  return names.filter((name) => {
    const event = catalog.find((item) => item.name === name);
    return event?.selectable !== false && !event?.requires_zone;
  });
}

function sourceLabel(device: DiscoveryCamera): string {
  return device.source_type === "gateway" ? "영상 Gateway" : "ONVIF 네트워크 카메라";
}

function displayStreamUrl(value: string): string {
  return value
    .replace(/:\/\/[^@]+@/, "://")
    .replace(/\?.*/, "?[redacted]")
    .replace(/#.*/, "#[redacted]");
}

function OnvifProfilePicker({
  discoveryId,
  username,
  password,
  onSelect,
}: {
  discoveryId: string;
  username: string;
  password: string;
  onSelect: (url: string) => void;
}) {
  const [profiles, setProfiles] = useState<OnvifProfile[]>([]);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");
  const [manualFallback, setManualFallback] = useState(false);

  async function load() {
    setLoading(true);
    setMessage("");
    try {
      const response = await fetchWithTimeout("/api/cameras/onvif-profiles", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ discovery_id: discoveryId, username, password }),
      });
      const data = (await response.json().catch(() => ({}))) as {
        available?: boolean;
        profiles?: OnvifProfile[];
        reason?: string;
        detail?: string;
      };
      if (!response.ok || data.available === false) {
        throw new Error(data.detail ?? data.reason ?? "ONVIF 프로필을 조회할 수 없습니다.");
      }
      const available = data.profiles?.filter(
        (profile): profile is OnvifProfile & { rtsp_url: string } => Boolean(profile.rtsp_url),
      ) ?? [];
      setProfiles(available);
      if (available[0]) onSelect(available[0].rtsp_url);
      if (!available.length) {
        setMessage("사용 가능한 Media Profile이 없습니다. 선택한 카메라의 RTSP 주소를 직접 입력하세요.");
        setManualFallback(true);
      }
    } catch (error) {
      const detail = redactCameraMessage(
        error instanceof Error ? error.message : "프로필 조회 중 오류가 발생했습니다.",
        password,
      );
      setMessage(`${detail} 선택한 카메라의 RTSP 주소를 직접 입력하세요.`);
      setManualFallback(true);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-3 rounded-lg border bg-muted/20 p-3">
      <div>
        <p className="text-sm font-medium">분석 스트림 선택</p>
        <p className="text-xs text-muted-foreground">카메라 계정으로 Media Profile을 읽어 H.264 서브스트림을 선택합니다.</p>
      </div>
      <Button type="button" className="min-h-11" variant="outline" disabled={loading || !discoveryId} onClick={load}>
        {loading ? <LoaderCircle className="animate-spin" /> : <RefreshCw />}
        ONVIF 스트림 프로필 조회
      </Button>
      {profiles.length > 0 && (
        <label className="block space-y-1 text-sm font-medium">
          분석 스트림
          <select className="min-h-11 w-full rounded-lg border bg-background px-3" onChange={(event) => onSelect(event.target.value)}>
            {profiles.map((profile) => (
              <option key={profile.token ?? profile.rtsp_url} value={profile.rtsp_url}>
                {profile.name ?? profile.token}
                {profile.resolution ? ` · ${profile.resolution[0]}x${profile.resolution[1]}` : ""}
                {profile.fps ? ` · ${profile.fps} FPS` : ""}
                {profile.encoding ? ` · ${profile.encoding}` : ""}
              </option>
            ))}
          </select>
        </label>
      )}
      {message && <p role="status" className="text-sm text-muted-foreground">{message}</p>}
      {manualFallback && <label className="block space-y-1 text-sm font-medium">RTSP 주소 직접 입력<Input aria-label="RTSP 주소 직접 입력" className="min-h-11" onChange={(event) => onSelect(event.target.value)} placeholder="rtsp://..." /></label>}
    </div>
  );
}

type BatchStatus = "pending" | "running" | "succeeded" | "failed";
type BatchItem = { device: DiscoveryCamera; status: BatchStatus; error?: string };

function BatchEnrollment({ policyProfiles, eventCatalog, profilePresets, onClose }: {
  policyProfiles: Record<string, PolicyEvent[]>;
  eventCatalog: EventCatalogItem[];
  profilePresets: Record<string, { label?: string; default_events: string[] }>;
  onClose: () => void;
}) {
  const domains = Object.keys(profilePresets).length > 0 ? Object.keys(profilePresets) : Object.keys(policyProfiles);
  const [devices, setDevices] = useState<DiscoveryCamera[]>([]);
  const [selected, setSelected] = useState<number[]>([]);
  const [items, setItems] = useState<BatchItem[]>([]);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [location, setLocation] = useState("");
  const [domain, setDomain] = useState(domains[0] ?? "");
  const [usePreset, setUsePreset] = useState(false);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    void fetchWithTimeout("/api/cameras/discover", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ protocol: "all" }) })
      .then(async (response) => {
        const data = await response.json().catch(() => ({})) as { devices?: DiscoveryCamera[]; reason?: string };
        if (!response.ok) throw new Error(data.reason ?? "카메라 검색을 시작할 수 없습니다.");
        setDevices(data.devices ?? []);
      })
      .catch((value) => setError(value instanceof Error ? value.message : "검색 중 오류가 발생했습니다."))
      .finally(() => setLoading(false));
  }, []);

  const deferred = (profilePresets[domain]?.default_events ?? []).map((name) => eventCatalog.find((event) => event.name === name)).filter((event): event is EventCatalogItem => Boolean(event?.requires_zone));
  const completed = items.filter((item) => item.status === "succeeded").length;
  function toggle(index: number) {
    setSelected((current) => current.includes(index) ? current.filter((value) => value !== index) : current.length < 16 ? [...current, index] : current);
  }
  async function enroll(retryOnly = false) {
    const queue = retryOnly ? items.filter((item) => item.status === "failed") : selected.map((index) => ({ device: devices[index], status: "pending" as const }));
    if (!queue.length) return;
    if (!retryOnly) setItems(queue);
    setRunning(true);
    setError("");
    let failures = false;
    for (const queued of queue) {
      setItems((current) => current.map((item) => item.device === queued.device ? { ...item, status: "running", error: undefined } : item));
      try {
        let rtspUrl = queued.device.rtsp_url ?? "";
        if (!rtspUrl && queued.device.discovery_id) {
          const profileResponse = await fetchWithTimeout("/api/cameras/onvif-profiles", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ discovery_id: queued.device.discovery_id, username, password }) });
          const profileData = await profileResponse.json().catch(() => ({})) as { profiles?: OnvifProfile[]; detail?: string; reason?: string };
          rtspUrl = profileData.profiles?.find((profile) => profile.rtsp_url)?.rtsp_url ?? "";
          if (!profileResponse.ok || !rtspUrl) throw new Error(profileData.detail ?? profileData.reason ?? "사용 가능한 ONVIF 스트림 프로필이 없습니다.");
        }
        if (!rtspUrl) throw new Error("등록할 RTSP 주소가 없습니다. 이 카메라는 개별 등록에서 RTSP 주소를 입력하세요.");
        const response = await fetch("/api/cameras", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: queued.device.name ?? queued.device.host ?? "카메라", location: location || undefined, rtsp_url: rtspUrl, username: username || undefined, password: password || undefined, profile: domain, enabled_events: usePreset ? registrationDefaults(profilePresets[domain]?.default_events ?? [], eventCatalog) : [], zones: [] }) });
        const data = await response.json().catch(() => ({})) as { detail?: string };
        if (!response.ok) throw new Error(data.detail ?? "카메라를 등록할 수 없습니다.");
        setItems((current) => current.map((item) => item.device === queued.device ? { ...item, status: "succeeded" } : item));
      } catch (value) {
        failures = true;
        const message = redactCameraMessage(value instanceof Error ? value.message : "등록 중 오류가 발생했습니다.", password);
        setItems((current) => current.map((item) => item.device === queued.device ? { ...item, status: "failed", error: message } : item));
      }
    }
    if (!failures) setPassword("");
    setRunning(false);
  }

  return <Card className="max-w-4xl"><CardHeader><CardTitle>여러 카메라 일괄 등록</CardTitle></CardHeader><CardContent className="space-y-4">
    <p className="text-sm text-muted-foreground">한 번에 최대 16대를 순서대로 등록합니다. 비밀번호는 화면에 표시하지 않으며 요청에만 사용됩니다.</p>
    {loading ? <p role="status">카메라를 검색하고 있습니다...</p> : <div className="grid gap-2 sm:grid-cols-2">{devices.map((device, index) => <label key={`${device.host}-${index}`} className="flex min-h-11 items-center gap-3 rounded-lg border p-3 text-sm"><input className="size-5" type="checkbox" checked={selected.includes(index)} disabled={!selected.includes(index) && selected.length >= 16} onChange={() => toggle(index)} /><span>{device.name ?? device.host ?? "검색된 카메라"}</span></label>)}</div>}
    {selected.length >= 16 && <p role="status" className="text-sm text-amber-700">일괄 등록은 최대 16대입니다. 추가 장비는 다음 일괄 등록에서 선택하세요.</p>}
    <p className="text-sm font-medium">등록 예정 {selected.length}대 / 최대 16대 {items.length > 0 && `· 현재 완료 ${completed} / ${items.length}대`}</p>
    <label className="block space-y-1 text-sm font-medium">공통 위치 <span className="font-normal text-muted-foreground">(선택)</span><Input className="min-h-11" value={location} onChange={(event) => setLocation(event.target.value)} /></label>
    <label className="block space-y-1 text-sm font-medium">공통 사용자 이름 <span className="font-normal text-muted-foreground">(선택)</span><Input autoComplete="username" className="min-h-11" value={username} onChange={(event) => setUsername(event.target.value)} /></label>
    <label className="block space-y-1 text-sm font-medium">공통 비밀번호 <span className="font-normal text-muted-foreground">(선택)</span><Input autoComplete="current-password" type="password" className="min-h-11" value={password} onChange={(event) => setPassword(event.target.value)} /></label>
    <label className="flex min-h-11 items-center gap-3 text-sm font-medium"><input className="size-5" type="checkbox" checked={usePreset} onChange={(event) => setUsePreset(event.target.checked)} />공통 감지 기본값 적용</label>
    {usePreset && <label className="block space-y-1 text-sm font-medium">현장 유형<select className="min-h-11 w-full rounded-lg border bg-background px-3" value={domain} onChange={(event) => setDomain(event.target.value)}>{domains.map((item) => <option key={item} value={item}>{profilePresets[item]?.label ?? item}</option>)}</select></label>}
    {usePreset && deferred.length > 0 && <p role="status" className="rounded-lg border border-amber-500/50 bg-amber-500/10 p-3 text-sm">영역이 필요한 기본 감지는 보류됩니다: {deferred.map((event) => `${event.label ?? event.name} (필요 영역: ${event.requires_zone})`).join(", ")}. 카메라 등록 후 영역을 설정한 뒤 활성화하세요.</p>}
    {items.length > 0 && <div className="space-y-2">{items.map((item, index) => { const label = item.device.name ?? item.device.host ?? "카메라"; const status = item.status === "pending" ? "대기" : item.status === "running" ? "등록 중" : item.status === "succeeded" ? "완료" : `실패 - ${item.error}`; return <div key={`${item.device.host}-${index}`} aria-label={`${label}: ${status}`} className="rounded-lg border p-3 text-sm"><strong>{label}</strong>: {status}</div>; })}</div>}
    {error && <p role="alert">{error}</p>}
    <div className="flex flex-wrap gap-2"><Button className="min-h-11" disabled={running || selected.length === 0} onClick={() => void enroll()}>{running && <LoaderCircle className="animate-spin" />} 선택한 카메라 등록</Button>{items.some((item) => item.status === "failed") && <Button className="min-h-11" variant="outline" disabled={running} onClick={() => void enroll(true)}>실패한 카메라만 재시도</Button>}<Button className="min-h-11" variant="ghost" disabled={running} onClick={onClose}>닫기</Button></div>
  </CardContent></Card>;
}

export function CameraOnboarding({
  policyProfiles,
  eventCatalog = [],
  profilePresets = {},
}: {
  policyProfiles: Record<string, PolicyEvent[]>;
  eventCatalog?: EventCatalogItem[];
  profilePresets?: Record<string, { label?: string; default_events: string[] }>;
}) {
  const [open, setOpen] = useState(false);
  const [batchOpen, setBatchOpen] = useState(false);
  const [step, setStep] = useState(0);
  const [mode, setMode] = useState<"automatic" | "manual">("automatic");
  const [name, setName] = useState("");
  const [location, setLocation] = useState("");
  const [rtspUrl, setRtspUrl] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [devices, setDevices] = useState<DiscoveryCamera[]>([]);
  const [selectedDevice, setSelectedDevice] = useState<number | null>(null);
  const [discovering, setDiscovering] = useState(false);
  const [testing, setTesting] = useState(false);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState("");
  const [test, setTest] = useState<StreamTest | null>(null);
  const [applyResult, setApplyResult] = useState<ApplyResult | null>(null);
  const [usbCapability, setUsbCapability] = useState<SourceCapability | null>(null);
  const domains = Object.keys(profilePresets).length > 0 ? Object.keys(profilePresets) : Object.keys(policyProfiles);
  const initialDomain = domains[0] ?? "";
  const [domain, setDomain] = useState(initialDomain);
  const [events, setEvents] = useState<string[]>(
    registrationDefaults(profilePresets[initialDomain]?.default_events ?? [], eventCatalog),
  );
  const selected = selectedDevice === null ? undefined : devices[selectedDevice];
  const streamUrl = rtspUrl;
  const catalogEvents: EventCatalogItem[] = eventCatalog.length > 0
    ? eventCatalog
    : (policyProfiles[domain] ?? [])
      .filter((event) => event.enabled !== false)
       .map((event) => ({ ...event, label: event.name, selectable: true, reason: null }));
  const presetEvents = profilePresets[domain]?.default_events ?? (policyProfiles[domain] ?? []).map((event) => event.name);
  const deferredPresetEvents = presetEvents.map((item) => catalogEvents.find((event) => event.name === item)).filter((event): event is EventCatalogItem => Boolean(event?.requires_zone));
  const canNext = step === 0
    ? mode === "manual" ? Boolean(name.trim() && streamUrl) : Boolean(selected && name.trim())
    : step === 1
      ? selected?.source_type !== "onvif" || Boolean(streamUrl)
      : step === 2
        ? Boolean(test?.ok ?? test?.connected)
        : step === 3 ? Boolean(domain) : true;

  const showError = (value: unknown, fallback: string) => setError(redactCameraMessage(
    value instanceof Error ? value.message : fallback,
    password,
  ));
  const setConnectionUrl = (value: string) => {
    setRtspUrl(value);
    setTest(null);
  };
  const applyDefaults = () => setEvents(registrationDefaults(presetEvents, catalogEvents));

  async function discover() {
    setDiscovering(true);
    setError("");
    setTest(null);
    try {
      const response = await fetchWithTimeout("/api/cameras/discover", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ protocol: "all" }),
      });
      const data = (await response.json().catch(() => ({}))) as {
        available?: boolean;
        devices?: DiscoveryCamera[];
        reason?: string;
      };
      if (!response.ok || data.available === false) {
        throw new Error(data.reason ?? "카메라 검색을 시작할 수 없습니다.");
      }
      const found = data.devices ?? [];
      setDevices(found);
      setSelectedDevice(null);
      setName("");
      setConnectionUrl("");
    } catch (value) {
      setDevices([]);
      showError(value, "검색 중 오류가 발생했습니다.");
    } finally {
      setDiscovering(false);
    }
  }

  function loadCapabilities() {
    void fetch("/api/video-sources/capabilities")
      .then(async (response) => response.ok ? response.json() : null)
      .then((data: { adapters?: SourceCapability[] } | null) => {
        setUsbCapability(data?.adapters?.find((adapter) => adapter.kind === "v4l2") ?? null);
      })
      .catch(() => setUsbCapability(null));
  }

  function openWizard() {
    setOpen(true);
    loadCapabilities();
    void discover(); // Discovery runs from the engine network, never the installer browser.
  }

  function selectDevice(index: number) {
    const device = devices[index];
    setSelectedDevice(index);
    setName(device.name ?? device.host ?? "카메라");
    setConnectionUrl(device.source_type === "gateway" ? device.rtsp_url ?? "" : "");
  }

  function reset() {
    setOpen(false);
    setStep(0);
    setMode("automatic");
    setError("");
    setTest(null);
    setPassword("");
    setApplyResult(null);
  }

  async function testStream() {
    setTesting(true);
    setError("");
    setTest(null);
    try {
      const response = await fetchWithTimeout("/api/cameras/test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ rtsp_url: streamUrl, username, password }),
      });
      const data = (await response.json().catch(() => ({}))) as StreamTest;
      if (!response.ok) throw new Error(data.detail ?? "스트림 검사에 실패했습니다.");
      setTest(data);
      if (!(data.ok ?? data.connected)) {
        setError(redactCameraMessage(data.detail ?? "연결할 수 없습니다. 진단 정보를 확인하세요.", password));
      }
    } catch (value) {
      showError(value, "스트림 검사 중 오류가 발생했습니다.");
    } finally {
      setTesting(false);
    }
  }

  async function apply() {
    setApplying(true);
    setError("");
    try {
      const response = await fetch("/api/cameras", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name,
          location: location || undefined,
          rtsp_url: streamUrl,
          username: username || undefined,
          password: password || undefined,
          profile: domain,
          enabled_events: events,
          zones: [],
        }),
      });
      const data = (await response.json().catch(() => ({}))) as ApplyResult & { detail?: string };
      if (!response.ok) throw new Error(data.detail ?? "카메라를 등록할 수 없습니다.");
      setApplyResult(data);
      setPassword("");
      setStep(5);
    } catch (value) {
      showError(value, "등록 중 오류가 발생했습니다.");
    } finally {
      setApplying(false);
    }
  }

  async function retryApply() {
    const cameraId = applyResult?.camera?.id;
    if (!cameraId) return;
    setApplying(true);
    setError("");
    try {
      const response = await fetch(`/api/cameras/${encodeURIComponent(cameraId)}/action`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "apply" }),
      });
      const data = (await response.json().catch(() => ({}))) as { detail?: string; applied?: boolean; apply_error?: string | null };
      if (!response.ok || data.applied !== true) {
        throw new Error(data.detail ?? data.apply_error ?? "실행 환경에 적용하지 못했습니다.");
      }
      setApplyResult((current) => ({ ...(current ?? {}), applied: true, apply_error: null }));
    } catch (value) {
      showError(value, "설정 재적용 중 오류가 발생했습니다.");
    } finally {
      setApplying(false);
    }
  }

  if (batchOpen) return <BatchEnrollment policyProfiles={policyProfiles} eventCatalog={eventCatalog} profilePresets={profilePresets} onClose={() => setBatchOpen(false)} />;
  if (!open) return <div className="flex gap-2"><Button className="min-h-11" onClick={openWizard}><Plus /> 카메라 추가</Button><Button className="min-h-11" variant="outline" onClick={() => setBatchOpen(true)}>여러 대 등록</Button></div>;

  if (step === 5) {
    const ready = applyResult?.applied === true;
    return (
      <Card className="max-w-3xl">
        <CardContent className="space-y-4 py-8 text-center">
          {ready ? <Check className="mx-auto size-9 text-primary" aria-hidden="true" /> : <TriangleAlert className="mx-auto size-9 text-amber-600" aria-hidden="true" />}
          <h2 className="text-xl font-semibold">{ready ? "카메라 운영 준비가 완료되었습니다" : "카메라는 저장됐지만 실행 준비에 실패했습니다"}</h2>
          <p className="text-sm text-muted-foreground"><strong>{name}</strong>의 설정과 자격 증명은 안전하게 저장되었습니다. {ready ? "영상 수신 Pipeline과 Gateway 적용도 확인했습니다." : `원인: ${applyResult?.apply_error ?? "실행 서비스 상태를 확인하세요."}`}</p>
          {error && <p role="alert" className="rounded-lg border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">{error}</p>}
            <p className="text-sm text-muted-foreground">영역이 필요한 감지 항목은 카메라 카드에서 영역을 설정한 뒤 활성화할 수 있습니다.</p>
            <div className="flex flex-wrap justify-center gap-2">
            {!ready && applyResult?.camera?.id && <Button className="min-h-11" variant="outline" disabled={applying} onClick={retryApply}>{applying && <LoaderCircle className="animate-spin" />} 실행 적용 재시도</Button>}
             {applyResult?.camera?.id && <a className="flex min-h-11 items-center rounded-lg border px-3 font-medium" href={`/cameras/${encodeURIComponent(applyResult.camera.id)}?focus=zones`}>영역 설정</a>}
             {applyResult?.camera?.id && <a className="flex min-h-11 items-center rounded-lg border px-3 font-medium" href={`/cameras/${encodeURIComponent(applyResult.camera.id)}?focus=detection`}>감지 설정</a>}
             <Button className="min-h-11" variant="outline" onClick={() => { setStep(0); setApplyResult(null); setName(""); setLocation(""); setRtspUrl(""); void discover(); }}>카메라 한 대 더 등록</Button>
             <a className="flex min-h-11 items-center rounded-lg border px-3 font-medium" href="/commissioning">커미셔닝으로 이동</a>
             <Button className="min-h-11" onClick={() => window.location.reload()}>카메라 목록으로</Button>
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="max-w-4xl">
      <CardHeader className="gap-3 border-b">
        <div className="flex items-center justify-between gap-3">
          <CardTitle>카메라 추가</CardTitle>
          <Button aria-label="온보딩 닫기" className="size-11" variant="ghost" onClick={reset}><X /></Button>
        </div>
        <ol className="grid grid-cols-5 gap-1" aria-label="카메라 등록 단계">
          {STEPS.map((label, index) => (
            <li key={label} className={`min-w-0 border-t-2 pt-1 text-xs ${index <= step ? "border-primary text-foreground" : "border-border text-muted-foreground"}`} aria-current={index === step ? "step" : undefined}>
              {index + 1}. <span className="hidden sm:inline">{label}</span>
            </li>
          ))}
        </ol>
      </CardHeader>
      <CardContent className="space-y-5 py-5">
        {error && <div role="alert" className="rounded-lg border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">{error}</div>}
        {step === 0 && (
          <section className="space-y-4">
            <div className="space-y-1">
              <h2 className="font-medium">네트워크에서 카메라 찾기</h2>
              <p className="text-sm text-muted-foreground">같은 CCTV 네트워크의 ONVIF 카메라와 연결된 영상 Gateway를 자동으로 검색합니다.</p>
            </div>
            {mode === "automatic" ? (
              <>
                <div className="flex flex-wrap items-center gap-2">
                  <Button className="min-h-11" variant="outline" disabled={discovering} onClick={() => void discover()}>
                    {discovering ? <LoaderCircle className="animate-spin" /> : <RefreshCw />}
                    카메라 다시 검색
                  </Button>
                  <Badge variant="outline">검색은 Appliance에서 실행됩니다</Badge>
                </div>
                {discovering && <div role="status" aria-live="polite" aria-busy="true" className="rounded-lg border border-dashed p-5 text-sm text-muted-foreground">ONVIF 및 Gateway 영상을 검색하고 있습니다...</div>}
                {!discovering && devices.length > 0 && (
                  <div className="grid gap-3 sm:grid-cols-2">
                    {devices.map((device, index) => {
                      const active = selectedDevice === index;
                      return (
                        <button
                          key={`${device.source_type ?? "onvif"}-${device.onvif_url ?? device.rtsp_url ?? device.host ?? index}`}
                          type="button"
                          className={`min-h-28 rounded-lg border p-4 text-left transition-colors ${active ? "border-primary bg-primary/5 ring-1 ring-primary" : "hover:bg-muted/50"}`}
                          aria-pressed={active}
                          onClick={() => selectDevice(index)}
                        >
                          <div className="flex items-start justify-between gap-2">
                            <div className="flex min-w-0 items-center gap-2 font-medium">
                              {device.source_type === "gateway" ? <Router className="size-4 shrink-0" /> : <Video className="size-4 shrink-0" />}
                              <span className="truncate">{device.name ?? device.host ?? "검색된 카메라"}</span>
                            </div>
                            <Badge variant={active ? "default" : "outline"}>{sourceLabel(device)}</Badge>
                          </div>
                          <p className="mt-2 text-sm text-muted-foreground">{device.model || device.host || "모델 정보 확인 필요"}</p>
                          {device.host && <p className="mt-1 text-xs text-muted-foreground">{device.host}{device.onvif_port && device.onvif_port !== 80 ? `:${device.onvif_port}` : ""}</p>}
                        </button>
                      );
                    })}
                  </div>
                )}
                {!discovering && devices.length === 0 && !error && <div className="rounded-lg border border-dashed p-5 text-sm text-muted-foreground">검색된 새 카메라가 없습니다. 카메라 전원, VLAN, ONVIF 설정을 확인한 뒤 다시 검색하세요.</div>}
                <div className="rounded-lg border bg-muted/30 p-4">
                  <div className="flex gap-3">
                    <Usb className="mt-0.5 size-5 shrink-0 text-muted-foreground" />
                    <div className="space-y-1 text-sm">
                      <p className="font-medium">로컬 USB 카메라</p>
                      <p className="text-muted-foreground">{usbCapability?.available ? "지원 준비 중입니다." : usbCapability?.reason ?? "현재 버전은 USB 장치 lifecycle adapter 검증 전입니다. RTSP bridge로 연결하세요."}</p>
                    </div>
                    <Badge className="ml-auto h-fit" variant="outline">준비 중</Badge>
                  </div>
                </div>
                <button type="button" className="text-left text-sm font-medium text-primary underline-offset-4 hover:underline" onClick={() => { setMode("manual"); setSelectedDevice(null); setError(""); }}>
                  카메라를 찾지 못했나요? RTSP 주소 직접 입력
                </button>
              </>
            ) : (
              <div className="space-y-4">
                <div className="rounded-lg border bg-muted/30 p-4 text-sm text-muted-foreground">수동 RTSP 등록은 ONVIF 또는 Gateway 검색이 불가능한 카메라와 NVR 채널을 위한 fallback입니다.</div>
                <label className="block space-y-1 text-sm font-medium">카메라 이름<Input className="min-h-11" value={name} onChange={(event) => setName(event.target.value)} /></label>
                <label className="block space-y-1 text-sm font-medium">위치 <span className="font-normal text-muted-foreground">(선택)</span><Input className="min-h-11" value={location} onChange={(event) => setLocation(event.target.value)} /></label>
                <label className="block space-y-1 text-sm font-medium">RTSP 주소<Input className="min-h-11" value={rtspUrl} onChange={(event) => setConnectionUrl(event.target.value)} /></label>
                <Button className="min-h-11" variant="outline" onClick={() => { setMode("automatic"); void discover(); }}><ChevronLeft /> 자동 검색으로 돌아가기</Button>
              </div>
            )}
            {mode === "automatic" && selected && <div className="grid gap-4 sm:grid-cols-2"><label className="block space-y-1 text-sm font-medium">등록 이름<Input className="min-h-11" value={name} onChange={(event) => setName(event.target.value)} /></label><label className="block space-y-1 text-sm font-medium">위치 <span className="font-normal text-muted-foreground">(선택)</span><Input className="min-h-11" value={location} onChange={(event) => setLocation(event.target.value)} /></label></div>}
          </section>
        )}
        {step === 1 && (
          <section className="space-y-4">
            <div className="space-y-1"><h2 className="font-medium">카메라 자격 증명</h2><p className="text-sm text-muted-foreground">입력한 비밀번호는 검사와 등록 요청에만 사용하며 이후 화면에 표시하지 않습니다.</p></div>
            <label className="block space-y-1 text-sm font-medium">사용자 이름 <span className="font-normal text-muted-foreground">(선택)</span><Input aria-label="사용자 이름" autoComplete="username" className="min-h-11" value={username} onChange={(event) => { setUsername(event.target.value); setTest(null); }} /></label>
            <label className="block space-y-1 text-sm font-medium">비밀번호 <span className="font-normal text-muted-foreground">(선택)</span><Input aria-label="비밀번호" autoComplete="current-password" className="min-h-11" type="password" value={password} onChange={(event) => { setPassword(event.target.value); setTest(null); }} /></label>
            {selected?.source_type === "onvif" && selected.discovery_id && <OnvifProfilePicker discoveryId={selected.discovery_id} username={username} password={password} onSelect={setConnectionUrl} />}
            {selected?.source_type === "gateway" && <div className="rounded-lg border bg-muted/20 p-3 text-sm text-muted-foreground"><MonitorUp className="mr-2 inline size-4" />선택한 Gateway 영상은 연결된 RTSP 경로를 사용합니다.</div>}
          </section>
        )}
        {step === 2 && (
          <section className="space-y-4">
            <h2 className="font-medium">스트림 검사</h2>
            <p className="text-sm text-muted-foreground">첫 프레임, 해상도, FPS를 확인한 뒤에만 등록할 수 있습니다.</p>
            <Button className="min-h-11" disabled={testing} onClick={() => void testStream()}>{testing ? <LoaderCircle className="animate-spin" /> : <RefreshCw />} 스트림 검사 실행</Button>
            {test && <div className="space-y-3 rounded-lg border p-3"><Badge variant={(test.ok ?? test.connected) ? "default" : "destructive"}>{(test.ok ?? test.connected) ? "연결 성공" : "연결 실패"}</Badge><dl className="grid gap-2 text-sm sm:grid-cols-2">{Object.entries(test.diagnostics ?? {}).map(([key, value]) => <div key={key}><dt className="text-muted-foreground">{key}</dt><dd className="break-all font-medium">{String(value ?? "-")}</dd></div>)}</dl></div>}
          </section>
        )}
        {step === 3 && (
          <section className="space-y-4">
            <h2 className="font-medium">감지 설정</h2>
            <div className="space-y-2"><label className="block space-y-1 text-sm font-medium">현장 유형<select className="min-h-11 w-full rounded-lg border bg-background px-3" value={domain} onChange={(event) => setDomain(event.target.value)}>{domains.map((item) => <option key={item} value={item}>{profilePresets[item]?.label ?? item}</option>)}</select></label><Button className="min-h-11" type="button" variant="outline" onClick={applyDefaults}>이 현장 유형의 기본값 적용</Button><p className="text-sm text-muted-foreground">현장 유형은 추천 기본값입니다. 실제 감지는 아래에서 선택한 항목만 활성화됩니다. 기본값 적용은 현재 선택을 교체합니다.</p></div>
            <fieldset className="space-y-2"><legend className="text-sm font-medium">감지 항목</legend>{catalogEvents.map((event) => { const selectable = event.selectable !== false; const needsZone = Boolean(event.requires_zone); const blockedReason = event.blocked_reason ?? event.reason ?? "이번 릴리스에서 사용할 수 없습니다."; return <label key={event.name} className={`flex min-h-11 items-center gap-3 rounded-lg border px-3 text-sm ${selectable && !needsZone ? "" : "opacity-60"}`} title={needsZone ? "카메라 등록 후 영역을 먼저 설정하세요." : selectable ? event.reason ?? undefined : blockedReason}><input className="size-5 accent-primary" type="checkbox" checked={events.includes(event.name)} disabled={(!selectable && !events.includes(event.name)) || needsZone} onChange={() => setEvents((current) => current.includes(event.name) ? current.filter((item) => item !== event.name) : [...current, event.name])} /><span>{event.label ?? event.name}</span>{!selectable && <Badge variant="outline">{event.release_qualified === false ? "릴리스 미자격" : "준비 중"}</Badge>}{needsZone && <Badge variant="outline">영역 설정 후 사용</Badge>}</label>; })}</fieldset>
            {catalogEvents.some((event) => event.release_qualified === false) && <p className="text-sm text-muted-foreground">릴리스 미자격 시험 기능은 이 릴리스에서 선택하거나 실행할 수 없습니다.</p>}
            {deferredPresetEvents.length > 0 && <p role="status" className="rounded-lg border border-amber-500/50 bg-amber-500/10 p-3 text-sm">기본값 중 영역이 필요한 감지는 등록 후로 보류됩니다: {deferredPresetEvents.map((event) => `${event.label ?? event.name} (필요 영역: ${event.requires_zone})`).join(", ")}. 영역이 아직 없어서 지금 활성화할 수 없습니다.</p>}
            <p className="text-sm text-muted-foreground">선택하지 않으면 영상만 수신합니다. 영역 기반 감지는 등록 후 현재 프레임에서 영역을 설정해야 활성화할 수 있습니다.</p>
          </section>
        )}
        {step === 4 && (
          <section className="space-y-4">
            <h2 className="font-medium">검토 및 적용</h2>
            <dl className="grid gap-3 rounded-lg border p-4 text-sm sm:grid-cols-2"><div><dt className="text-muted-foreground">카메라</dt><dd>{name}</dd></div><div><dt className="text-muted-foreground">위치</dt><dd>{location || "미지정"}</dd></div><div><dt className="text-muted-foreground">연결 방식</dt><dd>{selected ? sourceLabel(selected) : "수동 RTSP"}</dd></div><div><dt className="text-muted-foreground">스트림</dt><dd className="break-all">{displayStreamUrl(streamUrl)}</dd></div><div><dt className="text-muted-foreground">자격 증명</dt><dd>{username || password ? `사용자 이름: ${username || "없음"}, 비밀번호: ${password ? "입력됨" : "없음"}` : "인증 없음"}</dd></div><div><dt className="text-muted-foreground">프로파일</dt><dd>{domain}</dd></div><div><dt className="text-muted-foreground">이벤트</dt><dd>{events.join(", ") || "영상 수신만"}</dd></div></dl>
          </section>
        )}
        <div className="flex items-center justify-between gap-3 border-t pt-4">
          <Button className="min-h-11" variant="outline" disabled={step === 0 || testing || applying} onClick={() => { setError(""); setStep((value) => value - 1); }}><ChevronLeft /> 이전</Button>
          {step === 4 ? <Button className="min-h-11" disabled={applying} onClick={() => void apply()}>{applying && <LoaderCircle className="animate-spin" />} 등록 적용</Button> : <Button className="min-h-11" disabled={!canNext || testing} onClick={() => { setError(""); setStep((value) => value + 1); }}>다음 <ChevronRight /></Button>}
        </div>
      </CardContent>
    </Card>
  );
}
