import { LiveWall } from "@/components/live/live-wall";
import { Card, CardContent } from "@/components/ui/card";
import { getCameraTelemetry, getCameras, getCommissioning, getEvent, getIncident, getIncidents } from "@/lib/engine";
import { getLatestActiveCameraEvents } from "@/lib/live-risk";

export const dynamic = "force-dynamic";

export default async function LivePage({
  searchParams,
}: {
  searchParams: Promise<{ camera?: string; event?: string }>;
}) {
  const params = await searchParams;
  const [cameraResponse, telemetryResponse, commissioning, incidentResponse, incidentDetail, incidentEvent] =
    await Promise.all([
      getCameras(),
      getCameraTelemetry(),
      getCommissioning(),
      getIncidents({ unresolved: true, limit: 1000 }),
      params.event ? getIncident(params.event) : Promise.resolve(null),
      params.event ? getEvent(params.event) : Promise.resolve(null),
    ]);
  const cameras = (cameraResponse?.cameras ?? []).filter(
    (camera) => camera.managed && camera.enabled && camera.id,
  );
  const telemetry = telemetryResponse?.cameras ?? {};
  const initialFocusCameraId = cameras.some((camera) => camera.id === params.camera)
    ? params.camera ?? null
    : null;

  const latestEvents = getLatestActiveCameraEvents(incidentResponse?.incidents ?? []);
  const site = commissioning?.site.name ?? null;

  return (
    <div className="flex h-full flex-col" data-workspace>
      {cameras.length === 0 ? (
        <div className="flex flex-1 items-center justify-center p-8">
          <Card>
            <CardContent className="py-12 text-center text-sm text-muted-foreground">
              재생할 운영 카메라가 없습니다. 카메라를 등록하고 활성화하세요.
            </CardContent>
          </Card>
        </div>
      ) : (
        <LiveWall
          site={site}
          cameras={cameras}
          telemetry={telemetry}
          latestEvents={latestEvents}
          telemetryStatus={telemetryResponse?.telemetry_status}
          telemetryGeneratedAt={telemetryResponse?.generated_at}
          initialFocusCameraId={initialFocusCameraId}
          incidentEventId={params.event}
          incident={incidentDetail?.incident}
          incidentEvent={incidentEvent}
        />
      )}
    </div>
  );
}
