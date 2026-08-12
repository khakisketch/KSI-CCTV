"use client";

import { useEffect, useState } from "react";

const formatter = new Intl.DateTimeFormat("ko-KR", {
  month: "2-digit",
  day: "2-digit",
  weekday: "short",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

export function OperationClock() {
  const [now, setNow] = useState<Date | null>(null);

  useEffect(() => {
    const initial = setTimeout(() => setNow(new Date()), 0);
    const timer = setInterval(() => setNow(new Date()), 30_000);
    return () => {
      clearTimeout(initial);
      clearInterval(timer);
    };
  }, []);

  return <span className="tabular-nums">{now ? formatter.format(now) : "--.-- --:--"}</span>;
}
