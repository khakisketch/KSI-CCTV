"use client";

import { LogOut } from "lucide-react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";

export function LogoutButton() {
  const router = useRouter();
  async function logout() {
    await fetch("/api/auth/logout", { method: "POST" });
    router.push("/login");
    router.refresh();
  }
  return (
    <Button variant="ghost" size="icon" aria-label="로그아웃" onClick={logout}>
      <LogOut className="h-5 w-5" />
    </Button>
  );
}
