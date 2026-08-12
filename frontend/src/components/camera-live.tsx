"use client";

import { useEffect, useState } from "react";

import { CameraPlayer } from "@/components/camera-player";
import { Button } from "@/components/ui/button";
import { TrackingOverlay } from "@/components/tracking-overlay";

// 카드 안의 라이브 토글 island. 기본은 버튼만 — 클릭 시에만 플레이어를 마운트해
// 게이트웨이가 시청자 있을 때만 카메라를 풀하도록(on-demand) 한다(유휴 대역폭 0).
//
// LiveWall에서는 initialOn/controlledOn/hideChrome를 통해 자동재생과 외부 제어를 지원한다:
//   - initialOn: 진입 시 한 번만 재생할지(사용자가 끄기 전까지는 유지)
//   - controlledOn: 외부에서 on/off를 강제(레이아웃/자동재생 수 변경 시 wall이 결정)
//   - hideChrome: 플레이어 영역만 노출(토글 버튼은 wall 측에서 별도 렌더)
export interface CameraLiveProps {
  cameraId: string;
  enabled: boolean;
  trackingEnabled?: boolean;
  initialOn?: boolean;
  controlledOn?: boolean;
  hideChrome?: boolean;
  onActiveChange?: (active: boolean) => void;
}

export function CameraLive({
  cameraId,
  enabled,
  trackingEnabled = false,
  initialOn = false,
  controlledOn,
  hideChrome = false,
  onActiveChange,
}: CameraLiveProps) {
  const [on, setOn] = useState(initialOn);
  const [overlayOn, setOverlayOn] = useState(false);
  const [prevControlledOn, setPrevControlledOn] = useState<boolean | undefined>(controlledOn);

  // controlledOn이 전달되면 외부 상태가 우선한다(정렬/레이아웃 변경 시 wall이 강제).
  // "Adjusting state when a prop changes" 패턴(useEffect 사용 대신 렌더 중 보정)으로
  // setState-in-effect 캐스케이드를 피한다. 참고: https://react.dev/learn/you-might-not-need-an-effect
  if (controlledOn !== undefined && controlledOn !== prevControlledOn) {
    setPrevControlledOn(controlledOn);
    setOn(controlledOn);
  }
  // controlledOn이 해제된 경우(undefined) 보정 추적도 해제.
  if (controlledOn === undefined && prevControlledOn !== undefined) {
    setPrevControlledOn(undefined);
  }

  useEffect(() => {
    onActiveChange?.(on);
  }, [on, onActiveChange]);

  if (!enabled) {
    return <div className="text-xs text-muted-foreground">비활성 카메라 — 라이브 불가</div>;
  }
  if (!on) {
    return hideChrome ? null : (
      <Button className="max-sm:min-h-11" size="sm" variant="secondary" onClick={() => setOn(true)}>
        라이브 보기
      </Button>
    );
  }
  return (
    <div className="space-y-2">
      <CameraPlayer cameraId={cameraId} overlay={overlayOn ? <TrackingOverlay cameraId={cameraId} /> : undefined} />
      {!hideChrome && (
        <div className="flex flex-wrap gap-2">
          {trackingEnabled && (
            <Button
              className="max-sm:min-h-11"
              size="sm"
              variant={overlayOn ? "secondary" : "outline"}
              onClick={() => setOverlayOn((current) => !current)}
            >
              Tracking 표시 {overlayOn ? "끄기" : "켜기"}
            </Button>
          )}
          <Button className="max-sm:min-h-11" size="sm" variant="ghost" onClick={() => setOn(false)}>
            라이브 중지
          </Button>
        </div>
      )}
    </div>
  );
}
