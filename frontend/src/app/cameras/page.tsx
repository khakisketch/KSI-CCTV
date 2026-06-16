import { CameraLive } from "@/components/camera-live";
import { EngineStatus } from "@/components/engine-status";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { getCameras } from "@/lib/engine";

export const dynamic = "force-dynamic";

export default async function CamerasPage() {
  const res = await getCameras();
  const connected = res !== null;
  const cameras = res?.cameras ?? [];

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-2xl font-semibold tracking-tight">카메라</h1>
        <EngineStatus connected={connected} />
      </div>
      <p className="text-sm text-muted-foreground" style={{ wordBreak: "keep-all" }}>
        등록 카메라 목록입니다. 활성 카메라는 &ldquo;라이브 보기&rdquo;로 영상 게이트웨이를 통한 실시간 영상(HLS)을 재생합니다.
      </p>

      {cameras.length === 0 ? (
        <Card>
          <CardContent className="py-10 text-center text-sm text-muted-foreground">
            {connected ? "등록된 카메라가 없습니다." : "엔진 API에 연결되지 않았습니다."}
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {cameras.map((c, i) => (
            <Card key={c.id ?? i}>
              <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                <CardTitle className="text-base">{c.name ?? c.id ?? "카메라"}</CardTitle>
                <Badge variant={c.enabled ? "default" : "outline"}>
                  {c.enabled ? "활성" : "비활성"}
                </Badge>
              </CardHeader>
              <CardContent className="space-y-2 text-sm text-muted-foreground">
                <div style={{ wordBreak: "keep-all" }}>{c.location ?? "위치 미지정"}</div>
                {c.detection_type && <Badge variant="secondary">{c.detection_type}</Badge>}
                <div className="truncate text-xs">{c.stream_url || "스트림 URL 미설정"}</div>
                {c.id && <CameraLive cameraId={c.id} enabled={Boolean(c.enabled)} />}
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
