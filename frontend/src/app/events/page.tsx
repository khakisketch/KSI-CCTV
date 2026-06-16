import { EngineStatus } from "@/components/engine-status";
import { EventsLive } from "@/components/events-live";
import { getEvents } from "@/lib/engine";

export const dynamic = "force-dynamic";

export default async function EventsPage() {
  const res = await getEvents(200);
  const connected = res !== null;
  const events = res?.events ?? [];

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-2xl font-semibold tracking-tight">이벤트</h1>
        <EngineStatus connected={connected} />
      </div>
      <EventsLive initialEvents={events} connected={connected} />
    </div>
  );
}
