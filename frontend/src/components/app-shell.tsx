"use client";

import {
  Bell,
  Boxes,
  Camera,
  ClipboardCheck,
  FileCheck2,
  LayoutDashboard,
  MonitorPlay,
  MoreHorizontal,
  ScrollText,
  Settings,
  ShieldCheck,
  TriangleAlert,
} from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { LogoutButton } from "@/components/logout-button";
import { OperationClock } from "@/components/operation-clock";
import { ThemeToggle } from "@/components/theme-toggle";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";

const INSTALLER_NAV = [
  { href: "/", label: "Signal", icon: LayoutDashboard },
  { href: "/incidents", label: "사건 대기열", icon: TriangleAlert },
  { href: "/live", label: "라이브", icon: MonitorPlay },
  { href: "/cameras", label: "카메라", icon: Camera },
  { href: "/commissioning", label: "설치 진행", icon: ClipboardCheck },
  { href: "/events", label: "이벤트 기록", icon: Bell },
  { href: "/diagnostics", label: "진단", icon: ShieldCheck },
  { href: "/handoff", label: "인수인계", icon: FileCheck2 },
];
const ADMIN_NAV = [
  { href: "/models", label: "모델·프롬프트", icon: Boxes },
  { href: "/audit", label: "감사 로그", icon: ScrollText },
  { href: "/settings", label: "설정", icon: Settings },
];
const NAV = [...INSTALLER_NAV, ...ADMIN_NAV];
// Four direct links plus More keeps the mobile navigation at five touch targets.
// The incident queue, rather than investigation history, is the time-sensitive operator flow.
const PRIMARY_NAV = [INSTALLER_NAV[0], INSTALLER_NAV[1], INSTALLER_NAV[2], INSTALLER_NAV[3]];
const SECONDARY_NAV = [INSTALLER_NAV[4], INSTALLER_NAV[5], INSTALLER_NAV[6], INSTALLER_NAV[7], ...ADMIN_NAV];

function isActiveRoute(pathname: string, href: string): boolean {
  return href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(`${href}/`);
}

export type ShellMode = "workspace" | "document";

export function AppShell({
  children,
  authMode,
  devAuthBypass = false,
  mode = "document",
  statusStrip,
}: {
  children: React.ReactNode;
  authMode: "open" | "environment" | "local" | "error";
  devAuthBypass?: boolean;
  mode?: ShellMode;
  statusStrip?: React.ReactNode;
}) {
  const pathname = usePathname();
  const currentPage = NAV.find(({ href }) => isActiveRoute(pathname, href));
  if (pathname === "/login") return <>{children}</>;
  return (
    <div className="flex h-dvh overflow-hidden" data-wall-shell>
      <a href="#main-content" className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-background focus:px-3 focus:py-2 focus:text-sm focus:shadow">
        본문으로 건너뛰기
      </a>
      <aside data-wall-hide className="hidden w-56 shrink-0 flex-col border-r bg-sidebar text-sidebar-foreground md:flex">
        <div className="flex h-13 items-center gap-2 border-b px-4">
          <ShieldCheck className="h-5 w-5 text-primary" />
          <span className="font-semibold tracking-tight">KSI-CCTV</span>
        </div>
        <nav className="flex-1 overflow-y-auto space-y-0.5 p-2" aria-label="주요 탐색">
          {INSTALLER_NAV.map(({ href, label, icon: Icon }) => {
            const active = isActiveRoute(pathname, href);
            return (
              <Link
                key={href}
                href={href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors",
                  active
                    ? "bg-sidebar-accent text-sidebar-accent-foreground font-medium"
                    : "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-foreground",
                )}
              >
                <Icon className="h-4 w-4 shrink-0" />
                <span className="truncate" style={{ wordBreak: "keep-all" }}>{label}</span>
              </Link>
            );
          })}
          <p className="px-3 pt-4 pb-1 text-xs font-medium text-muted-foreground">관리자</p>
          {ADMIN_NAV.map(({ href, label, icon: Icon }) => {
            const active = isActiveRoute(pathname, href);
            return (
              <Link
                key={href}
                href={href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors",
                  active ? "bg-sidebar-accent text-sidebar-accent-foreground font-medium" : "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-foreground",
                )}
              >
                <Icon className="h-4 w-4 shrink-0" />
                <span className="truncate" style={{ wordBreak: "keep-all" }}>{label}</span>
              </Link>
            );
          })}
        </nav>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header data-wall-hide className="flex h-13 shrink-0 items-center justify-between border-b px-4">
          <div className="flex items-center gap-2 md:hidden">
            <ShieldCheck className="h-5 w-5 text-primary" />
            <span className="font-semibold">{currentPage?.label ?? "KSI-CCTV"}</span>
          </div>
          <div className="hidden items-center gap-3 text-sm md:flex">
            <span className="font-medium">{currentPage?.label ?? "통합 관제"}</span>
            {statusStrip && (
              <>
                <span className="h-4 w-px bg-border" />
                {statusStrip}
              </>
            )}
            <span className="h-4 w-px bg-border" />
            <span className="text-xs text-muted-foreground"><OperationClock /></span>
          </div>
          <div className="flex items-center gap-1">
            <ThemeToggle />
            {!devAuthBypass && authMode !== "open" && <LogoutButton />}
          </div>
        </header>
        {devAuthBypass && (
          <div
            data-wall-hide
            role="alert"
            className="flex min-h-9 items-center justify-center gap-2 border-b border-amber-500/50 bg-amber-500/15 px-4 py-1.5 text-center text-xs font-semibold text-amber-700 dark:text-amber-300"
          >
            <TriangleAlert className="h-3.5 w-3.5 shrink-0" />
            개발 모드 · 로그인 비활성화 · loopback 전용
          </div>
        )}
        {authMode === "open" && (
          <div
            data-wall-hide
            role="alert"
            className="flex min-h-9 items-center justify-center gap-2 border-b border-warning/40 bg-warning/10 px-4 py-1.5 text-center text-xs font-semibold text-foreground"
          >
            <TriangleAlert className="h-3.5 w-3.5 shrink-0" />
            <span>접근 보호 꺼짐: 모든 사용자가 카메라와 설정을 변경할 수 있습니다.</span>
            <Link href="/settings" className="min-h-9 content-center underline underline-offset-2">설정</Link>
          </div>
        )}
        <main
          id="main-content"
          className={cn(
            "flex-1",
            mode === "workspace" ? "overflow-hidden" : "overflow-auto p-4 md:p-5",
          )}
        >
          {children}
        </main>
        <nav data-wall-hide className="flex shrink-0 items-stretch border-t bg-background md:hidden" aria-label="주요 메뉴">
          {PRIMARY_NAV.map(({ href, label, icon: Icon }) => {
            const active = isActiveRoute(pathname, href);
            return (
              <Link
                key={href}
                href={href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "flex min-h-14 flex-1 flex-col items-center justify-center gap-0.5 text-[11px]",
                  active ? "text-primary" : "text-muted-foreground",
                )}
              >
                <Icon className="h-5 w-5" />
                <span style={{ wordBreak: "keep-all" }}>{label}</span>
              </Link>
            );
          })}
          <DropdownMenu>
            <DropdownMenuTrigger
              aria-label="더보기 메뉴"
              className={cn(
                "flex min-h-14 flex-1 flex-col items-center justify-center gap-0.5 text-[11px] outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
                SECONDARY_NAV.some(({ href }) => isActiveRoute(pathname, href))
                  ? "text-primary"
                  : "text-muted-foreground",
              )}
            >
              <MoreHorizontal className="h-5 w-5" />
              <span>더보기</span>
            </DropdownMenuTrigger>
            <DropdownMenuContent side="top" align="end" sideOffset={8} className="w-48">
              {SECONDARY_NAV.map(({ href, label, icon: Icon }) => (
                <DropdownMenuItem
                  key={href}
                  render={<Link href={href} />}
                  className={cn("min-h-11 gap-3 px-3", isActiveRoute(pathname, href) && "font-medium text-primary")}
                >
                  <Icon className="h-4 w-4" />
                  {label}
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
        </nav>
      </div>
    </div>
  );
}
