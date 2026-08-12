import { Suspense } from "react";

import { EventsLive } from "@/components/events-live";
import { getCameras, getEvents, getIncidents } from "@/lib/engine";

export const dynamic = "force-dynamic";

export default async function EventsPage() {
  const [res, cameraResponse, incidentResponse] = await Promise.all([
    getEvents({ limit: 200 }),
    getCameras(),
    getIncidents({ limit: 1000 }),
  ]);
  const connected = res !== null;
  const events = res?.events ?? [];
  const cameraNames = Object.fromEntries(
    (cameraResponse?.cameras ?? []).filter((camera) => camera.id).map((camera) => [camera.id!, camera.name ?? camera.id!]),
  );

  return (
    <Suspense
      fallback={
        <div className="flex h-full items-center justify-center">
          <p className="text-sm text-muted-foreground">이벤트 데이터를 불러오는 중…</p>
        </div>
      }
    >
      <EventsLive
        initialEvents={events}
        initialIncidents={incidentResponse?.incidents ?? []}
        connected={connected}
        cameraNames={cameraNames}
      />
    </Suspense>
  );
}
