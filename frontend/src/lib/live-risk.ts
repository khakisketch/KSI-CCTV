import type { EventItem, IncidentItem } from "@/lib/engine";

/** Live-wall emphasis is reserved for active operator incidents, never immutable event history alone. */
export function getLatestActiveCameraEvents(incidents: IncidentItem[]): Record<string, EventItem | undefined> {
  const latestEvents: Record<string, EventItem | undefined> = {};
  for (const incident of incidents) {
    if (incident.status === "resolved" || !incident.camera_id || !isHighPriority(incident.source_event_alert_level)) continue;
    const event: EventItem = {
      event_id: incident.event_id,
      camera_id: incident.camera_id,
      event_type: incident.source_event_type ?? undefined,
      alert_level: incident.source_event_alert_level,
      timestamp: incident.updated_at,
    };
    const previous = latestEvents[incident.camera_id];
    if (previous && !isMoreRecentOrCritical(event, previous)) continue;
    latestEvents[incident.camera_id] = event;
  }
  return latestEvents;
}

function isHighPriority(alertLevel?: string | null): alertLevel is "high" | "critical" {
  return alertLevel === "high" || alertLevel === "critical";
}

function isMoreRecentOrCritical(next: EventItem, previous: EventItem): boolean {
  const nextCritical = next.alert_level === "critical";
  const previousCritical = previous.alert_level === "critical";
  if (nextCritical && !previousCritical) return true;
  if (!nextCritical && previousCritical) return false;
  const nextTime = next.timestamp ? Date.parse(next.timestamp) : NaN;
  const previousTime = previous.timestamp ? Date.parse(previous.timestamp) : NaN;
  if (Number.isNaN(nextTime) || Number.isNaN(previousTime)) return nextCritical;
  return nextTime > previousTime;
}
