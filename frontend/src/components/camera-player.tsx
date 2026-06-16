"use client";

import { useEffect, useRef, useState } from "react";

// 카메라 라이브 플레이어. /api/cameras/{id}/stream에서 HLS URL을 받아 재생한다.
// Safari는 네이티브 HLS, 그 외 브라우저는 hls.js(동적 import — 초기 번들 영향 최소).

type StreamInfo = { id: string; hls: string; webrtc: string | null };

export function CameraPlayer({ cameraId }: { cameraId: string }) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let destroyed = false;
    // hls.js 인스턴스(타입은 동적 import라 unknown 처리)
    let hls: { destroy: () => void } | null = null;

    (async () => {
      try {
        const res = await fetch(`/api/cameras/${encodeURIComponent(cameraId)}/stream`);
        if (!res.ok) {
          const j = (await res.json().catch(() => ({}))) as { detail?: string };
          throw new Error(j.detail || `스트림 정보를 가져오지 못했습니다(${res.status})`);
        }
        const info = (await res.json()) as StreamInfo;
        const video = videoRef.current;
        if (!video || destroyed) return;

        if (video.canPlayType("application/vnd.apple.mpegurl")) {
          // Safari/iOS — 네이티브 HLS
          video.src = info.hls;
        } else {
          const { default: Hls } = await import("hls.js");
          if (destroyed) return;
          if (Hls.isSupported()) {
            const inst = new Hls({ lowLatencyMode: true, backBufferLength: 30 });
            inst.loadSource(info.hls);
            inst.attachMedia(video);
            inst.on(Hls.Events.ERROR, (_evt, data) => {
              // 치명적 오류만 노출(비치명 오류는 hls.js가 자동 복구). 진단용 콘솔 로그 포함.
              if (data.fatal) {
                console.error("[CameraPlayer] HLS fatal", data.type, data.details);
                setErr("스트림 재생 오류 — 카메라가 오프라인이거나 게이트웨이에 발행 중이 아닐 수 있습니다.");
              }
            });
            hls = inst;
          } else {
            video.src = info.hls; // 최후 폴백
          }
        }
        setLoading(false);
      } catch (e) {
        if (!destroyed) {
          setErr(e instanceof Error ? e.message : "라이브 연결에 실패했습니다.");
          setLoading(false);
        }
      }
    })();

    return () => {
      destroyed = true;
      if (hls) hls.destroy();
    };
  }, [cameraId]);

  if (err) {
    return (
      <div
        className="grid aspect-video w-full place-items-center rounded-md bg-muted px-3 text-center text-xs text-muted-foreground"
        style={{ wordBreak: "keep-all" }}
      >
        {err}
      </div>
    );
  }

  return (
    <div className="relative aspect-video w-full overflow-hidden rounded-md bg-black">
      <video
        ref={videoRef}
        className="h-full w-full object-contain"
        muted
        autoPlay
        playsInline
        controls
      />
      {loading && (
        <div className="absolute inset-0 grid place-items-center text-xs text-white/70">
          연결 중…
        </div>
      )}
    </div>
  );
}
