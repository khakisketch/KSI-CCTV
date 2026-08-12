import type { EventItem } from "@/lib/engine";

export type VerificationTone = "default" | "secondary" | "destructive" | "outline";

export type VerificationPresentation = {
  label: "검증 대기" | "확인됨" | "오탐 가능" | "검증 불가";
  tone: VerificationTone;
  requiresAttention: boolean;
};

/** Normalizes the new verification lifecycle while retaining legacy alert-state events. */
export function getVerificationPresentation(event: EventItem): VerificationPresentation | null {
  switch (event.verification_state) {
    case "pending":
      return { label: "검증 대기", tone: "outline", requiresAttention: true };
    case "confirmed":
      return { label: "확인됨", tone: "default", requiresAttention: false };
    case "rejected":
      return { label: "오탐 가능", tone: "secondary", requiresAttention: false };
    case "unverified":
      // A failed verification is not a rejection. Keep its original safety risk prominent.
      return { label: "검증 불가", tone: "destructive", requiresAttention: true };
  }
  if (event.vlm_unconfirmed) return { label: "검증 불가", tone: "destructive", requiresAttention: true };
  if (event.alert_state === "confirmed") return { label: "확인됨", tone: "default", requiresAttention: false };
  if (event.alert_state === "likely_false_positive" || event.alert_state === "resolved_false_positive") {
    return { label: "오탐 가능", tone: "secondary", requiresAttention: false };
  }
  if (event.alert_state === "provisional" || event.alert_state === "needs_review") {
    return { label: "검증 대기", tone: event.alert_state === "needs_review" ? "destructive" : "outline", requiresAttention: true };
  }
  return null;
}

export function abbreviateDigest(digest?: string): string | null {
  if (!digest) return null;
  return digest.length > 16 ? `${digest.slice(0, 8)}...${digest.slice(-6)}` : digest;
}
