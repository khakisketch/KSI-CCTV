import { SiteSetupForm } from "@/components/site-setup-form";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { getCommissioning } from "@/lib/engine";

export const dynamic = "force-dynamic";

export default async function SetupPage() {
  const data = await getCommissioning();
  if (!data) return <p className="p-8 text-sm text-muted-foreground">엔진 연결을 확인한 뒤 다시 시도하세요.</p>;
  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">현장 정보</CardTitle>
        </CardHeader>
        <CardContent>
          <SiteSetupForm site={data.site} revision={data.revision} />
        </CardContent>
      </Card>
    </div>
  );
}
