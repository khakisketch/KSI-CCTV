import { GalleryView } from "@/components/gallery-view";
import { Card, CardContent } from "@/components/ui/card";
import { getCaptures } from "@/lib/engine";

export const dynamic = "force-dynamic";

export default async function GalleryPage() {
  const res = await getCaptures();
  const connected = res !== null;
  const dates = res?.dates ?? [];

  return connected ? (
    <GalleryView dates={dates} />
  ) : (
    <Card>
      <CardContent className="py-10 text-center text-sm text-muted-foreground">
        엔진 API에 연결되지 않았습니다.
      </CardContent>
    </Card>
  );
}
