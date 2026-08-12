import { Suspense } from "react";

import { SignalWorkspace } from "@/components/signal-workspace";
import type { SignalWorkspaceItem } from "@/components/signal-workspace";
import {
  getCameraTelemetry,
  getCameras,
  getCommissioning,
  getControlStatus,
  getEvents,
  getIncidents,
  isCameraReceiving,
} from "@/lib/engine";

export const dynamic = "force-dynamic";

export default async function SignalPage() {
  const [eventsRes, cameras, telemetryRes, control, commissioning, incidentsRes] = await Promise.all([
    getEvents(100),
    getCameras(),
    getCameraTelemetry(),
    getControlStatus(),
    getCommissioning(),
    getIncidents({ unresolved: true, limit: 100 }),
  ]);

  const connected = eventsRes !== null && cameras !== null;
  const allCameras = cameras?.cameras ?? [];
  const managedCameras = allCameras.filter((camera) => camera.managed && camera.enabled && camera.id);
  const telemetry = telemetryRes?.cameras ?? {};
  const healthyCameras = managedCameras.filter((camera) => isCameraReceiving(telemetry[camera.id!])).length;
  const eventsById = new Map(
    (eventsRes?.events ?? [])
      .filter((event) => event.event_id)
      .map((event) => [event.event_id!, event]),
  );
  const cameraById = new Map(
    allCameras.filter((camera) => camera.id).map((camera) => [camera.id!, camera]),
  );

  const signalItems: SignalWorkspaceItem[] = (incidentsRes?.incidents ?? []).map((incident) => {
    const camera = incident.camera_id ? cameraById.get(incident.camera_id) ?? null : null;
    return {
      incident,
      event: eventsById.get(incident.event_id) ?? null,
      camera,
      cameraName: camera?.name ?? incident.camera_id ?? "카메라 확인 필요",
      telemetry: incident.camera_id ? telemetry[incident.camera_id] : undefined,
    };
  });

  const metrics = {
    cameraHealth: connected ? `카메라 ${healthyCameras}/${managedCameras.length}` : "카메라 —",
    pipeline: !control?.connected ? "파이프라인 확인 필요" : control.running ? "파이프라인 가동" : "파이프라인 중지",
    installProgress: commissioning ? `설치 ${commissioning.progress.percent}%` : connected ? "설치 확인 필요" : "설치 —",
    siteName: commissioning?.site.name || "현장 미설정",
  };

  return (
    <Suspense
      fallback={
        <div className="flex h-full items-center justify-center">
          <p className="text-sm text-muted-foreground">신호 데이터를 불러오는 중…</p>
        </div>
      }
    >
      <SignalWorkspace
        initialItems={signalItems}
        available={incidentsRes?.available === true}
        metrics={metrics}
      />
    </Suspense>
  );
}
