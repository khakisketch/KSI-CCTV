import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

/** 구현 예정 화면 플레이스홀더(엔진 API 연동 단계에서 채움). */
export function ComingSoon({ title, note }: { title: string; note: string }) {
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
      <Card>
        <CardHeader>
          <CardTitle className="text-base">준비 중</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground" style={{ wordBreak: "keep-all" }}>
            {note}
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
