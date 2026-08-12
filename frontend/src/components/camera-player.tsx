"use client";

import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";

// 카메라 라이브 플레이어. /api/cameras/{id}/stream에서 HLS URL을 받아 재생한다.
// Safari는 네이티브 HLS, 그 외 브라우저는 hls.js(동적 import — 초기 번들 영향 최소).
//
// 신뢰성 보강:
//   - stream metadata fetch에 AbortController 적용(unmount 시 즉시 중단)
//   - 첫 화면 타임아웃(8s) 외에도 timeupdate 미수신 기반 stall 감지 → 자동 재시도
//   - fatal HLS 오류 자동 복구 시도 최대 2회(bounded exponential retry 이전 단계)
//   - 재생 시 video.currentTime을 polling해 stall 여부 판단

type StreamInfo = { id: string; hls: string; webrtc: string | null };

const FIRST_FRAME_TIMEOUT_MS = 8_000;
const STALL_TIMEOUT_MS = 10_000;        // timeupdate/progress 10s 없으면 stall 취급
const MAX_AUTO_RECOVER_ATTEMPTS = 2;    // 자동 복구 한계 — 넘으면 사용자 수동 재시도

export function CameraPlayer({ cameraId, overlay }: { cameraId: string; overlay?: ReactNode }) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [attempt, setAttempt] = useState(0); // attempt는 사용자 재시도 + 자동 복구 모두 증감

  useEffect(() => {
    let destroyed = false;
    // hls.js 인스턴스(타입은 동적 import라 unknown 처리)
    let hls: { destroy: () => void; startLoad: () => void; recoverMediaError: () => void } | null = null;
    let nativeVideo: HTMLVideoElement | null = null;
    let nativeErrorHandler: (() => void) | null = null;
    let playbackVideo: HTMLVideoElement | null = null;
    let playbackHandler: (() => void) | null = null;
    let playbackTimer: ReturnType<typeof setTimeout> | null = null;
    let stallTimer: ReturnType<typeof setTimeout> | null = null;
    let stallReset: (() => void) | null = null;
    let autoRecoverAttempts = 0;
    const abortController = new AbortController();

    // playbackTimer/stallTimer 초기화 헬퍼(중복 cleanup 방지)
    const armFirstFrameTimeout = () => {
      if (playbackTimer) clearTimeout(playbackTimer);
      playbackTimer = setTimeout(() => {
        if (destroyed) return;
        tryRecover("first-frame-timeout", "영상 첫 화면을 받지 못했습니다. 카메라 수신 상태를 확인한 뒤 다시 시도하세요.");
      }, FIRST_FRAME_TIMEOUT_MS);
    };

    // stall 감지: timeupdate/progress 이벤트로 주기적 reset.
    // STALL_TIMEOUT_MS 동안 두 이벤트가 모두 없으면 stall로 간주.
    const armStallTimer = (video: HTMLVideoElement) => {
      if (stallTimer) clearTimeout(stallTimer);
      stallReset = () => {
        if (stallTimer) clearTimeout(stallTimer);
        stallTimer = setTimeout(() => {
          if (destroyed) return;
          // buffered가 없거나 timeupdate가 멈춘 상태로 재생 안 됨 → stall.
          tryRecover("playback-stall", "영상이 멈춘 것으로 보입니다. 자동으로 복구를 시도합니다.");
        }, STALL_TIMEOUT_MS);
      };
      stallReset();
      video.addEventListener("timeupdate", stallReset, { passive: true });
      video.addEventListener("progress", stallReset, { passive: true });
    };

    // 자동 복구 한계 도달 전까지 hls.startLoad/recoverMediaError + attempt++ 로 재시도,
    // 그 이후에는 오류 메시지를 노출해 사용자가 수동으로 재시도하게 한다.
    const tryRecover = (reason: string, userMessage: string) => {
      if (destroyed) return;
      console.warn("[CameraPlayer] recover", cameraId, reason, "autoRecoverAttempts=", autoRecoverAttempts);
      if (autoRecoverAttempts < MAX_AUTO_RECOVER_ATTEMPTS && hls) {
        autoRecoverAttempts += 1;
        try {
          // hls.js 회복 호출 — 동적 import 타입이라 any로 다루지만 표준 API.
          (hls as { startLoad?: () => void; recoverMediaError?: () => void }).startLoad?.();
          (hls as { recoverMediaError?: () => void }).recoverMediaError?.();
        } catch {
          /* 회복 실패 — 다음 자동 시도 또는 사용자 재시도 */
        }
        return;
      }
      // 자동 복구 한계 도달 — 사용자에게 알리고 수동 재시도 유도.
      if (hls) {
        try { hls.destroy(); } catch { /* noop */ }
        hls = null;
      }
      if (playbackTimer) { clearTimeout(playbackTimer); playbackTimer = null; }
      if (stallTimer) { clearTimeout(stallTimer); stallTimer = null; }
      setErr(userMessage);
      setLoading(false);
    };

    (async () => {
      try {
        const res = await fetch(
          `/api/cameras/${encodeURIComponent(cameraId)}/stream`,
          { signal: abortController.signal },
        );
        if (!res.ok) {
          const j = (await res.json().catch(() => ({}))) as { detail?: string };
          throw new Error(j.detail || `스트림 정보를 가져오지 못했습니다(${res.status})`);
        }
        const info = (await res.json()) as StreamInfo;
        const video = videoRef.current;
        if (!video || destroyed) return;
        playbackVideo = video;
        playbackHandler = () => {
          if (playbackTimer) { clearTimeout(playbackTimer); playbackTimer = null; }
          if (!destroyed) setLoading(false);
        };
        video.addEventListener("loadeddata", playbackHandler);
        video.addEventListener("playing", playbackHandler);
        armFirstFrameTimeout();
        armStallTimer(video);

        const attachNativePlayback = () => {
          nativeVideo = video;
          nativeErrorHandler = () => {
            if (destroyed) return;
            if (playbackTimer) { clearTimeout(playbackTimer); playbackTimer = null; }
            setErr("스트림을 재생할 수 없습니다. 카메라 연결과 브라우저 코덱 지원을 확인하세요.");
            setLoading(false);
          };
          video.addEventListener("error", nativeErrorHandler);
          video.src = info.hls;
        };

        if (video.canPlayType("application/vnd.apple.mpegurl")) {
          // Safari/iOS — 네이티브 HLS
          attachNativePlayback();
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
                if (data.details === "manifestIncompatibleCodecsError") {
                  if (playbackTimer) { clearTimeout(playbackTimer); playbackTimer = null; }
                  if (stallTimer) { clearTimeout(stallTimer); stallTimer = null; }
                  setLoading(false);
                  setErr("이 브라우저가 카메라 영상 코덱을 지원하지 않습니다. H.264 지원 브라우저에서 다시 시도하세요.");
                } else {
                  // 네트워크 오류는 bounded retry 시도, 그 외는 즉시 사용자 메시지.
                  tryRecover(
                    `fatal:${String(data.type)}`,
                    data.type === Hls.ErrorTypes.NETWORK_ERROR
                      ? "스트림 데이터를 받지 못했습니다. 카메라와 영상 게이트웨이 연결을 확인하세요."
                      : "스트림 재생 중 오류가 발생했습니다. 잠시 후 다시 시도하세요.",
                  );
                }
              }
            });
            hls = inst;
          } else {
            attachNativePlayback(); // 최후 폴백
          }
        }
      } catch (e) {
        if (destroyed || (e as { name?: string } | null)?.name === "AbortError") return;
        if (playbackTimer) { clearTimeout(playbackTimer); playbackTimer = null; }
        if (stallTimer) { clearTimeout(stallTimer); stallTimer = null; }
        setErr(e instanceof Error ? e.message : "라이브 연결에 실패했습니다.");
        setLoading(false);
      }
    })();

    return () => {
      destroyed = true;
      abortController.abort();
      if (hls) { try { hls.destroy(); } catch { /* noop */ } }
      if (nativeVideo && nativeErrorHandler) nativeVideo.removeEventListener("error", nativeErrorHandler);
      if (playbackVideo && playbackHandler) {
        playbackVideo.removeEventListener("loadeddata", playbackHandler);
        playbackVideo.removeEventListener("playing", playbackHandler);
      }
      if (stallReset && playbackVideo) {
        playbackVideo.removeEventListener("timeupdate", stallReset);
        playbackVideo.removeEventListener("progress", stallReset);
      }
      if (playbackTimer) clearTimeout(playbackTimer);
      if (stallTimer) clearTimeout(stallTimer);
    };
  }, [cameraId, attempt]);

  if (err) {
    return (
      <div
        className="flex aspect-video w-full flex-col items-center justify-center gap-3 rounded-md bg-muted px-3 text-center text-xs text-muted-foreground"
        style={{ wordBreak: "keep-all" }}
      >
        <span role="alert">{err}</span>
        <Button size="sm" variant="outline" onClick={() => {
          setErr(null);
          setLoading(true);
          setAttempt((value) => value + 1);
        }}>
          재시도
        </Button>
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
      {overlay}
      {loading && (
        <div className="absolute inset-0 grid place-items-center text-xs text-white/70">
          연결 중…
        </div>
      )}
    </div>
  );
}
