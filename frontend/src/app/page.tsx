import { Activity, Bell, Camera, Cpu } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

const METRICS = [
  { label: "카메라", value: "0", sub: "연결됨", icon: Camera },
  { label: "오늘 이벤트", value: "0", sub: "최근 24시간", icon: Bell },
  { label: "활성 모델", value: "3", sub: "general · ppe · fire/smoke", icon: Cpu },
  { label: "엔진 상태", value: "대기", sub: "API 미연결", icon: Activity },
];

export default function DashboardPage() {
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">관제 대시보드</h1>
          <p className="text-sm text-muted-foreground" style={{ wordBreak: "keep-all" }}>
            실시간 안전 이벤트 현황. 학습 없이 텍스트 프롬프트로 객체를 탐지합니다.
          </p>
        </div>
        <Badge variant="outline" className="gap-1.5">
          <span className="h-2 w-2 rounded-full bg-amber-500" />
          엔진 API 미연결 (연동 예정)
        </Badge>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {METRICS.map(({ label, value, sub, icon: Icon }) => (
          <Card key={label}>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">{label}</CardTitle>
              <Icon className="h-4 w-4 text-muted-foreground" />
            </CardHeader>
            <CardContent>
              <div className="text-3xl font-bold tracking-tight">{value}</div>
              <p className="mt-1 text-xs text-muted-foreground" style={{ wordBreak: "keep-all" }}>
                {sub}
              </p>
            </CardContent>
          </Card>
        ))}
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">최근 이벤트</CardTitle>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>시각</TableHead>
                <TableHead>카메라</TableHead>
                <TableHead>유형</TableHead>
                <TableHead className="text-right">신뢰도</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              <TableRow>
                <TableCell
                  colSpan={4}
                  className="h-28 text-center text-sm text-muted-foreground"
                  style={{ wordBreak: "keep-all" }}
                >
                  아직 이벤트가 없습니다. 엔진을 연결하면 실시간 이벤트가 여기에 표시됩니다.
                </TableCell>
              </TableRow>
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}
