import { Suspense } from "react";

import { CameraOnboarding } from "@/components/camera-onboarding";
import { CameraManagementOverview } from "@/components/camera-management-overview";
import { Card, CardContent } from "@/components/ui/card";
import { getCameras, getEventCatalog, getPolicies } from "@/lib/engine";

export const dynamic = "force-dynamic";

type Ev = { name: string; alert_level?: string; enabled?: boolean };

export default async function CamerasPage() {
  const [res, policiesRes, catalogRes] = await Promise.all([
    getCameras(),
    getPolicies(),
    getEventCatalog(),
  ]);
  const connected = res !== null;
  const cameras = res?.cameras ?? [];
  const policyProfiles = Object.fromEntries(
    Object.entries((policiesRes?.profiles ?? {}) as Record<string, Ev[]>).map(
      ([name, events]) => [name, events.filter((event) => event.enabled !== false)],
    ),
  );
  const eventCatalog = catalogRes?.catalog ?? [];
  const profilePresets = catalogRes?.profiles ?? {};

  if (!connected) {
    return (
      <div className="space-y-4">
        <div className="flex items-center justify-end gap-2">
          <CameraOnboarding policyProfiles={policyProfiles} eventCatalog={eventCatalog} profilePresets={profilePresets} />
        </div>
        <Card>
          <CardContent className="py-10 text-center text-sm text-muted-foreground">
            엔진 API에 연결되지 않았습니다.
          </CardContent>
        </Card>
      </div>
    );
  }

  if (cameras.length === 0) {
    return (
      <div className="space-y-4">
        <div className="flex items-center justify-end gap-2">
          <CameraOnboarding policyProfiles={policyProfiles} eventCatalog={eventCatalog} profilePresets={profilePresets} />
        </div>
        <Card>
          <CardContent className="py-10 text-center text-sm text-muted-foreground">
            등록된 카메라가 없습니다.
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <Suspense
      fallback={
        <div className="flex h-full items-center justify-center">
          <p className="text-sm text-muted-foreground">카메라 데이터를 불러오는 중…</p>
        </div>
      }
    >
      <CameraManagementOverview
        cameras={cameras}
        onboarding={<CameraOnboarding policyProfiles={policyProfiles} eventCatalog={eventCatalog} profilePresets={profilePresets} />}
      />
    </Suspense>
  );
}
