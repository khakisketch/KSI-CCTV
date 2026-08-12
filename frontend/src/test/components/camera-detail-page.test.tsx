import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

vi.mock("next/navigation", () => ({ notFound: vi.fn() }));
vi.mock("@/components/camera-live", () => ({ CameraLive: () => <div>live viewer</div> }));
vi.mock("@/components/camera-telemetry", () => ({
  CameraTelemetryProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  CameraTelemetry: () => <div>telemetry</div>,
}));
vi.mock("@/components/camera-settings", () => ({ CameraSettings: () => <div>detection settings</div> }));
vi.mock("@/components/camera-actions", () => ({ CameraActions: () => <div>zones and actions</div> }));
vi.mock("@/components/camera-connection-editor", () => ({ CameraConnectionEditor: () => <div>connection editor</div> }));
vi.mock("@/lib/engine", () => ({
  getCamera: vi.fn().mockResolvedValue({
    revision: 4,
    camera: { id: "cam-01", name: "정문", location: "1층", managed: true, enabled: true, enabled_events: ["line_crossing"] },
  }),
  getPolicies: vi.fn().mockResolvedValue({ profiles: { general: [{ name: "intrusion", enabled: true }] } }),
  getEventCatalog: vi.fn().mockResolvedValue({ catalog: [], profiles: {} }),
}));

import CameraDetailPage from "@/app/cameras/[id]/page";

describe("CameraDetailPage", () => {
  it("composes live, telemetry, detection, zones/actions, and connection management", async () => {
    render(await CameraDetailPage({ params: Promise.resolve({ id: "cam-01" }) }));

    expect(screen.getByText(/정문/)).toBeInTheDocument();
    expect(screen.getByText("live viewer")).toBeInTheDocument();
    expect(screen.getByText("telemetry")).toBeInTheDocument();
    expect(screen.getByText("detection settings")).toBeInTheDocument();
    expect(screen.getByText("zones and actions")).toBeInTheDocument();
    expect(screen.getByText("connection editor")).toBeInTheDocument();
  });
});
