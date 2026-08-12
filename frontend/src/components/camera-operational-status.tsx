import { CheckCircle2, CircleAlert, MapPin, Radio, ShieldAlert } from "lucide-react";

import type { CameraItem, CameraTelemetryItem } from "@/lib/engine";
import { isCameraReceiving } from "@/lib/engine";
import { cn } from "@/lib/utils";

type StatusTone = "ready" | "attention" | "unknown";

type OperationalStatus = {
  label: string;
  value: string;
  detail: string;
  tone: StatusTone;
  icon: typeof Radio;
};

export function CameraOperationalStatus({
  camera,
  telemetry,
  className,
}: {
  camera?: CameraItem | null;
  telemetry?: CameraTelemetryItem;
  className?: string;
}) {
  const statuses = getOperationalStatuses(camera, telemetry);

  return (
    <dl className={cn("grid gap-2 sm:grid-cols-3", className)} aria-label="카메라 운영 상태">
      {statuses.map(({ label, value, detail, tone, icon: Icon }) => (
        <div
          key={label}
          data-status={tone}
          className={cn(
            "rounded-lg border p-3",
            tone === "ready" && "border-emerald-500/35 bg-emerald-500/5",
            tone === "attention" && "border-amber-500/45 bg-amber-500/10",
            tone === "unknown" && "border-border bg-muted/30",
          )}
        >
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Icon
              className={cn(
                "size-4",
                tone === "ready" && "text-emerald-600 dark:text-emerald-400",
                tone === "attention" && "text-amber-700 dark:text-amber-300",
              )}
              aria-hidden="true"
            />
            <dt>{label}</dt>
          </div>
          <dd className="mt-2 flex items-center gap-1.5 font-medium">
            {tone === "ready" ? <CheckCircle2 className="size-4 text-emerald-600 dark:text-emerald-400" aria-hidden="true" /> : tone === "attention" ? <CircleAlert className="size-4 text-amber-700 dark:text-amber-300" aria-hidden="true" /> : null}
            {value}
          </dd>
          <p className="mt-1 text-xs leading-5 text-muted-foreground">{detail}</p>
        </div>
      ))}
    </dl>
  );
}

function getOperationalStatuses(camera?: CameraItem | null, telemetry?: CameraTelemetryItem): OperationalStatus[] {
  const video = isCameraReceiving(telemetry)
    ? { value: "정상", detail: "현재 프레임을 수신하고 있습니다.", tone: "ready" as const }
    : telemetry
      ? { value: telemetry.health_state === "disconnected" ? "단절" : "확인 필요", detail: "영상 수신 상태를 확인하세요.", tone: "attention" as const }
      : { value: "확인 필요", detail: "수신 상태 정보가 아직 없습니다.", tone: "unknown" as const };
  const zoneCount = camera?.zones?.length;
  const zones = !camera
    ? { value: "확인 필요", detail: "카메라 설정을 불러오지 못했습니다.", tone: "unknown" as const }
    : zoneCount
      ? { value: `${zoneCount}개 설정`, detail: "제한 구역이 저장되어 있습니다.", tone: "ready" as const }
      : { value: "미설정", detail: "제한 구역 설정이 필요합니다.", tone: "attention" as const };
  const enabledEventCount = camera?.enabled_events?.length;
  const detection = !camera
    ? { value: "확인 필요", detail: "감지 설정을 불러오지 못했습니다.", tone: "unknown" as const }
    : enabledEventCount
      ? { value: `${enabledEventCount}개 활성`, detail: "선택한 감지를 실행 중입니다.", tone: "ready" as const }
      : { value: "미설정", detail: "감지 항목을 활성화하세요.", tone: "attention" as const };

  return [
    { label: "영상 수신", icon: Radio, ...video },
    { label: "제한 구역", icon: MapPin, ...zones },
    { label: "감지 설정", icon: ShieldAlert, ...detection },
  ];
}
