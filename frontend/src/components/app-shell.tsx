"use client";

import { Bell, Boxes, Camera, LayoutDashboard, Settings, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { ThemeToggle } from "@/components/theme-toggle";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/", label: "대시보드", icon: LayoutDashboard },
  { href: "/events", label: "이벤트", icon: Bell },
  { href: "/cameras", label: "카메라", icon: Camera },
  { href: "/models", label: "모델·프롬프트", icon: Boxes },
  { href: "/settings", label: "설정", icon: Settings },
];

/** 데스크톱 관제 콘솔 레이아웃 — 좌측 사이드바(md+) + 상단 헤더. 모바일은 헤더만(경량 뷰어). */
export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  return (
    <div className="flex min-h-screen">
      {/* 사이드바 (데스크톱) */}
      <aside className="hidden w-60 shrink-0 flex-col border-r bg-sidebar text-sidebar-foreground md:flex">
        <div className="flex h-14 items-center gap-2 border-b px-4">
          <ShieldCheck className="h-5 w-5 text-primary" />
          <span className="font-semibold tracking-tight">KSI-CCTV</span>
        </div>
        <nav className="flex-1 space-y-1 p-3">
          {NAV.map(({ href, label, icon: Icon }) => {
            const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
            return (
              <Link
                key={href}
                href={href}
                className={cn(
                  "flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors",
                  active
                    ? "bg-sidebar-accent text-sidebar-accent-foreground font-medium"
                    : "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-foreground",
                )}
              >
                <Icon className="h-4 w-4" />
                <span style={{ wordBreak: "keep-all" }}>{label}</span>
              </Link>
            );
          })}
        </nav>
        <div className="border-t p-3 text-xs text-muted-foreground">설치형 AI CCTV 관제</div>
      </aside>

      {/* 본문 */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 items-center justify-between border-b px-4">
          <div className="flex items-center gap-2 md:hidden">
            <ShieldCheck className="h-5 w-5 text-primary" />
            <span className="font-semibold">KSI-CCTV</span>
          </div>
          <div className="hidden text-sm text-muted-foreground md:block">관제 대시보드</div>
          <ThemeToggle />
        </header>
        <main className="flex-1 overflow-auto p-4 md:p-6">{children}</main>
        {/* 모바일 하단 탭 (경량 내비) */}
        <nav className="flex items-center justify-around border-t md:hidden">
          {NAV.map(({ href, label, icon: Icon }) => {
            const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
            return (
              <Link
                key={href}
                href={href}
                className={cn(
                  "flex flex-1 flex-col items-center gap-0.5 py-2 text-[11px]",
                  active ? "text-primary" : "text-muted-foreground",
                )}
              >
                <Icon className="h-5 w-5" />
                <span style={{ wordBreak: "keep-all" }}>{label}</span>
              </Link>
            );
          })}
        </nav>
      </div>
    </div>
  );
}
