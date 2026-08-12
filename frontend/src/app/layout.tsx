import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";

import { AppShell } from "@/components/app-shell";
import { ThemeProvider } from "@/components/theme-provider";
import { Toaster } from "@/components/ui/sonner";
import { developmentAuthBypassState } from "@/lib/auth-mode";
import { getAuthStatus } from "@/lib/auth";

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
  return (
    <html
      lang="ko"
      suppressHydrationWarning
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full">
        <ThemeProvider attribute="class" defaultTheme="dark" enableSystem disableTransitionOnChange>
          <AppShell authMode={authMode} devAuthBypass={devAuthBypass}>{children}</AppShell>
          <Toaster />
        </ThemeProvider>
      </body>
    </html>
  );
}
