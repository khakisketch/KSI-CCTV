import { notFound } from "next/navigation";

import { CameraActions } from "@/components/camera-actions";
import { CameraConnectionEditor } from "@/components/camera-connection-editor";
import { CameraLive } from "@/components/camera-live";
import { CameraSettings } from "@/components/camera-settings";
import { CameraTelemetry, CameraTelemetryProvider } from "@/components/camera-telemetry";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { getCamera, getEventCatalog, getPolicies } from "@/lib/engine";

export const dynamic = "force-dynamic";

export default async function CameraDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const [data, policiesRes, catalogRes] = await Promise.all([getCamera(id), getPolicies(), getEventCatalog()]);
  if (!data?.camera || !data.camera.managed) notFound();

  const camera = data.camera;
  const policyProfiles = Object.fromEntries(
    Object.entries((policiesRes?.profiles ?? {}) as Record<string, Array<{ name: string; alert_level?: string; enabled?: boolean }>>)
      .map(([name, events]) => [name, events.filter((event) => event.enabled !== false)]),
  );
  const eventCatalog = catalogRes?.catalog ?? [];
  const profilePresets = catalogRes?.profiles ?? {};
  const trackingEnabled = (camera.enabled_events ?? []).some((event) => event === "line_crossing" || event === "loitering");

  return (
    <div className="mx-auto max-w-5xl space-y-4">
      <p className="text-sm text-muted-foreground">{camera.name ?? id} · {camera.location ?? "위치 미지정"}</p>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card><CardHeader><CardTitle className="text-base">라이브 영상</CardTitle></CardHeader><CardContent><CameraLive cameraId={id} enabled={Boolean(camera.enabled)} trackingEnabled={trackingEnabled} /></CardContent></Card>
        <Card><CardHeader><CardTitle className="text-base">연결 상태 및 텔레메트리</CardTitle></CardHeader><CardContent><CameraTelemetryProvider><CameraTelemetry cameraId={id} /></CameraTelemetryProvider></CardContent></Card>
      </div>
      <Card><CardHeader><CardTitle className="text-base">감지 설정</CardTitle></CardHeader><CardContent><CameraSettings cameraId={id} initialDomain={camera.profile ?? camera.detection_type} initialEvents={camera.enabled_events ?? []} policyProfiles={policyProfiles} eventCatalog={eventCatalog} profilePresets={profilePresets} zones={camera.zones} revision={data.revision} enabled={Boolean(camera.enabled)} /></CardContent></Card>
      <Card><CardHeader><CardTitle className="text-base">영역 및 운영 작업</CardTitle></CardHeader><CardContent><CameraActions cameraId={id} cameraName={camera.name} enabled={Boolean(camera.enabled)} zones={camera.zones} revision={data.revision} /></CardContent></Card>
      <CameraConnectionEditor camera={camera} revision={data.revision} />
    </div>
  );
}
