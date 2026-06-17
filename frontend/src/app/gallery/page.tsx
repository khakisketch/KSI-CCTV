import { EngineStatus } from "@/components/engine-status";
import { GalleryView } from "@/components/gallery-view";
import { Card, CardContent } from "@/components/ui/card";
import { getCaptures } from "@/lib/engine";

export const dynamic = "force-dynamic";

export default async function GalleryPage() {
  const res = await getCaptures();
  const connected = res !== null;
  const dates = res?.dates ?? [];

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-2xl font-semibold tracking-tight">갤러리</h1>
        <EngineStatus connected={connected} />
      </div>
      <p className="text-sm text-muted-foreground" style={{ wordBreak: "keep-all" }}>
        이벤트 발생 시 저장된 캡처 이미지입니다. 얼굴은 저장 시 블러 처리되며, 암호화 저장된 이미지는
        서버에서 복호화해 표시합니다(키 설정 시).
      </p>
      {connected ? (
        <GalleryView dates={dates} />
      ) : (
        <Card>
          <CardContent className="py-10 text-center text-sm text-muted-foreground">
            엔진 API에 연결되지 않았습니다.
          </CardContent>
        </Card>
      )}
    </div>
  );
}
