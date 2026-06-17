"use client";

import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import type { CaptureDate } from "@/lib/engine";

function fmtDate(d: string): string {
  return d.length === 8 ? `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6, 8)}` : d;
}

// 캡처 썸네일 — 이미지는 same-origin 프록시 경유. 암호화(.enc)인데 키 없으면 엔진이 404 →
// onError로 "복호화 키 필요" 안내(깨진 이미지 대신).
function CaptureThumb({ src, name, encrypted }: { src: string; name: string; encrypted: boolean }) {
  const [err, setErr] = useState(false);
  return (
    <figure className="space-y-1">
      {err ? (
        <div className="grid aspect-video place-items-center rounded-md bg-muted px-2 text-center text-xs text-muted-foreground">
          {encrypted ? "🔒 복호화 키 필요" : "불러오기 실패"}
        </div>
      ) : (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={src}
          alt={name}
          loading="lazy"
          onError={() => setErr(true)}
          className="aspect-video w-full rounded-md bg-black object-cover"
        />
      )}
      <figcaption className="truncate text-xs text-muted-foreground" style={{ wordBreak: "keep-all" }}>
        {name}
      </figcaption>
    </figure>
  );
}

export function GalleryView({ dates }: { dates: CaptureDate[] }) {
  const [active, setActive] = useState(dates[0]?.date ?? "");

  if (dates.length === 0) {
    return (
      <Card>
        <CardContent className="py-10 text-center text-sm text-muted-foreground">
          저장된 캡처 이미지가 없습니다. 알람이 발생하면 여기에 표시됩니다.
        </CardContent>
      </Card>
    );
  }

  const day = dates.find((d) => d.date === active) ?? dates[0];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        {dates.map((d) => (
          <Button
            key={d.date}
            size="sm"
            variant={d.date === day.date ? "default" : "outline"}
            onClick={() => setActive(d.date)}
          >
            {fmtDate(d.date)}
          </Button>
        ))}
      </div>

      {day.cameras.map((cam) => (
        <Card key={cam.camera_id}>
          <CardContent className="space-y-3 pt-6">
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium" style={{ wordBreak: "keep-all" }}>
                {cam.camera_id}
              </span>
              <Badge variant="secondary">{cam.count}장</Badge>
            </div>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
              {cam.images.map((img) => (
                <CaptureThumb
                  key={img.name}
                  src={`/api/captures/${day.date}/${encodeURIComponent(cam.camera_id)}/${encodeURIComponent(img.name)}`}
                  name={img.name}
                  encrypted={img.encrypted}
                />
              ))}
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
