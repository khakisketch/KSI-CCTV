"use client";

import { useState } from "react";

import { CameraPlayer } from "@/components/camera-player";
import { Button } from "@/components/ui/button";

// 카드 안의 라이브 토글 island. 기본은 버튼만 — 클릭 시에만 플레이어를 마운트해
// 게이트웨이가 시청자 있을 때만 카메라를 풀하도록(on-demand) 한다(유휴 대역폭 0).
export function CameraLive({ cameraId, enabled }: { cameraId: string; enabled: boolean }) {
  const [on, setOn] = useState(false);

  if (!enabled) {
    return <div className="text-xs text-muted-foreground">비활성 카메라 — 라이브 불가</div>;
  }
  if (!on) {
    return (
      <Button size="sm" variant="secondary" onClick={() => setOn(true)}>
        라이브 보기
      </Button>
    );
  }
  return (
    <div className="space-y-2">
      <CameraPlayer cameraId={cameraId} />
      <Button size="sm" variant="ghost" onClick={() => setOn(false)}>
        라이브 중지
      </Button>
    </div>
  );
}
