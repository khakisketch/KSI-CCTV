import { Suspense } from "react";

import { IncidentQueue } from "@/components/incident-queue";
import { getCameraTelemetry, getCameras, getEvents, getIncidents } from "@/lib/engine";

export const dynamic = "force-dynamic";

export default async function IncidentsPage({
  searchParams,
}: {
  searchParams: Promise<{ category?: string }>;
}) {
  const [incidentResponse, cameraResponse, eventResponse, telemetryResponse, params] = await Promise.all([
    getIncidents({ unresolved: true, limit: 1000 }),
    getCameras(),
    getEvents(250),
    getCameraTelemetry(),
    searchParams,
  ]);

  const incidents = incidentResponse?.incidents ?? [];
  const allCameras = cameraResponse?.cameras ?? [];
  const cameraNames = new Map(
    allCameras.filter((camera) => camera.id).map((camera) => [camera.id!, camera.name ?? camera.id!]),
  );
  const cameraById = new Map(
    allCameras.filter((camera) => camera.id).map((camera) => [camera.id!, camera]),
  );
  const telemetry = telemetryResponse?.cameras ?? {};
  const eventsById = new Map(
    (eventResponse?.events ?? [])
      .filter((event) => event.event_id)
      .map((event) => [event.event_id!, event]),
  );

  const items = incidents.map((incident) => ({
    incident,
    event: eventsById.get(incident.event_id) ?? null,
    cameraName: incident.camera_id
      ? cameraNames.get(incident.camera_id) ?? incident.camera_id
      : "카메라 확인 필요",
    camera: incident.camera_id ? cameraById.get(incident.camera_id) ?? null : null,
    telemetry: incident.camera_id ? telemetry[incident.camera_id] : undefined,
  }));

  return (
    <Suspense
      fallback={
        <div className="flex h-full items-center justify-center">
          <p className="text-sm text-muted-foreground">사건 데이터를 불러오는 중…</p>
        </div>
      }
    >
      <IncidentQueue
        available={incidentResponse?.available === true}
        initialCategory={params.category}
        items={items}
      />
    </Suspense>
  );
}
