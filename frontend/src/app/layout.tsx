import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";

import { cookies } from "next/headers";

import { AppShell } from "@/components/app-shell";
import { ThemeProvider } from "@/components/theme-provider";
import { Toaster } from "@/components/ui/sonner";
import { developmentAuthBypassState } from "@/lib/auth-mode";
import { getAuthStatus, SESSION_COOKIE, sessionPrincipal } from "@/lib/auth";

import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "KSI-CCTV 관제",
  description: "설치형 AI CCTV 안전 관제 — 학습 없이 텍스트로 즉시 탐지",
};

export default async function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  const devAuthBypass = developmentAuthBypassState().status === "active";
  const { mode: authMode } = await getAuthStatus();
  // R7: 서버에서 세션 주체를 해석해 내비게이션 역할을 전달한다.
  // open 모드·개발 bypass·미인증 상태는 기존 표시 유지(admin 기본값).
  const principal = await sessionPrincipal((await cookies()).get(SESSION_COOKIE)?.value);
  const role = principal?.role ?? "admin";
  return (
    <html
      lang="ko"
      suppressHydrationWarning
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full">
        <ThemeProvider attribute="class" defaultTheme="dark" enableSystem disableTransitionOnChange>
          <AppShell authMode={authMode} devAuthBypass={devAuthBypass} role={role}>{children}</AppShell>
          <Toaster />
        </ThemeProvider>
      </body>
    </html>
  );
}
