import { Badge } from "@/components/ui/badge";
import type { EventItem } from "@/lib/engine";
import { abbreviateDigest, getVerificationPresentation } from "@/lib/event-verification";

export function VerificationBadge({ event }: { event: EventItem }) {
  const verification = getVerificationPresentation(event);
  return verification ? <Badge variant={verification.tone}>{verification.label}</Badge> : null;
}

export function EventDecisionChain({ event }: { event: EventItem }) {
  const metadata = event.metadata;
  const hasBehavior = Boolean(metadata?.action || metadata?.model_names?.length || typeof metadata?.confidence === "number");
  const hasVerification = Boolean(event.verification_required || event.verification_state || event.verification_model || event.verification_reason_codes?.length);
  const hasSource = Boolean(event.source_models?.length || event.rule_name || hasBehavior);
  if (!hasSource && !hasVerification) return null;

  return (
    <section className="mb-3 border-t pt-3 text-sm" aria-label="판정 근거">
      <p className="font-medium">판정 근거</p>
      <div className="mt-1 space-y-1 text-xs text-muted-foreground">
        {event.source_models?.length ? <p>감지 모델: {event.source_models.join(", ")}</p> : null}
        {event.rule_name ? <p>규칙: {event.rule_name}</p> : null}
        {hasBehavior ? (
          <p>
            행동: {[metadata?.action, typeof metadata?.confidence === "number" ? `${Math.round(metadata.confidence * 100)}%` : null, metadata?.track_id !== undefined ? `추적 ${metadata.track_id}` : null].filter(Boolean).join(" · ")}
            {metadata?.model_names?.length ? ` (${metadata.model_names.join(", ")})` : ""}
          </p>
        ) : null}
        {hasVerification ? (
          <p>
            검증: {event.verification_mode ?? "single"} · {event.frame_count ?? 0} 프레임 · {event.confirm_count ?? 0} 확인 / {event.reject_count ?? 0} 오탐 / {event.error_count ?? 0} 오류
            {event.verification_quorum ? ` · 정족수 ${event.verification_quorum}` : ""}
            {event.verification_model ? ` · ${event.verification_model}` : ""}
            {abbreviateDigest(event.verification_model_digest) ? ` · ${abbreviateDigest(event.verification_model_digest)}` : ""}
          </p>
        ) : null}
        {event.verification_reason_codes?.length ? <p>사유: {event.verification_reason_codes.join(", ")}</p> : null}
      </div>
    </section>
  );
}
