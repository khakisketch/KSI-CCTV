import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CameraOperationalStatus } from "@/components/camera-operational-status";

describe("CameraOperationalStatus", () => {
  it("derives ready operational states from camera configuration and receiving telemetry", () => {
    render(<CameraOperationalStatus camera={{ id: "camera-1", zones: [{ id: "zone-1", type: "polygon", points: [] }], enabled_events: ["intrusion", "loitering"] }} telemetry={{ connected: true, health_state: "receiving", seconds_since_last_frame: 0.5 }} />);

    expect(screen.getByText("영상 수신").parentElement?.parentElement).toHaveAttribute("data-status", "ready");
    expect(screen.getByText("정상")).toBeInTheDocument();
    expect(screen.getByText("1개 설정")).toBeInTheDocument();
    expect(screen.getByText("2개 활성")).toBeInTheDocument();
  });

  it("calls out missing zones and enabled detections without inventing a pending count", () => {
    render(<CameraOperationalStatus camera={{ id: "camera-1", zones: [], enabled_events: [] }} telemetry={{ connected: false, health_state: "disconnected" }} />);

    expect(screen.getByText("단절")).toBeInTheDocument();
    expect(screen.getAllByText("미설정")).toHaveLength(2);
    expect(screen.queryByText(/보류/)).not.toBeInTheDocument();
  });
});
