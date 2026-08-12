import Link from "next/link";
import { Suspense } from "react";

import { IncidentDetail } from "@/components/incident-detail";
import { Card, CardContent } from "@/components/ui/card";
import { getCameraTelemetry, getCameras, getEvent, getIncident } from "@/lib/engine";

export const dynamic = "force-dynamic";

export default async function IncidentDetailPage({ params }: { params: Promise<{ eventId: string }> }) {
  const { eventId } = await params;
  const [detail, event, cameras, telemetry] = await Promise.all([
    getIncident(eventId),
    getEvent(eventId),
    getCameras(),
    getCameraTelemetry(),
  ]);

  if (!detail) {
    return (
      <div className="flex h-full items-center justify-center p-8">
        <Card>
          <CardContent className="space-y-3 py-12 text-center text-sm text-muted-foreground">
            <p>사건을 찾을 수 없거나 사건 저장소를 사용할 수 없습니다.</p>
            <Link className="text-primary underline-offset-4 hover:underline" href="/incidents">
              사건 대기열로 돌아가기
            </Link>
          </CardContent>
        </Card>
      </div>
    );
  }

  const camera = detail.incident.camera_id
    ? cameras?.cameras.find((item) => item.id === detail.incident.camera_id) ?? null
    : null;
  const cameraName = camera?.name ?? detail.incident.camera_id ?? "카메라 확인 필요";

  return (
    <Suspense
      fallback={
        <div className="flex h-full items-center justify-center">
          <p className="text-sm text-muted-foreground">사건 상세를 불러오는 중…</p>
        </div>
      }
    >
      <IncidentDetail
        initialIncident={{ ...detail.incident, actions: detail.actions }}
        event={event}
        cameraName={cameraName}
        camera={camera}
        telemetry={detail.incident.camera_id ? telemetry?.cameras[detail.incident.camera_id] : undefined}
      />
    </Suspense>
  );
}
