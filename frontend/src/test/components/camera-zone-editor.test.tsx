import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { CameraZoneEditor } from "@/components/camera-zone-editor";

vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn() }) }));

describe("CameraZoneEditor", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("uses the registered camera snapshot proxy and saves zones through its same-origin route", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: vi.fn().mockResolvedValue({}) } as unknown as Response);
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    render(<CameraZoneEditor cameraId="cam_001" />);

    await user.click(screen.getByRole("button", { name: "영역 편집" }));
    expect(screen.getByRole("img", { name: "카메라 스냅샷" })).toHaveAttribute(
      "src",
      expect.stringContaining("/api/cameras/cam_001/snapshot?t=0"),
    );

    const canvas = screen.getByRole("button", { name: "카메라 스냅샷 영역 편집" });
    vi.spyOn(canvas, "getBoundingClientRect").mockReturnValue({ left: 0, top: 0, width: 100, height: 100 } as DOMRect);
    fireEvent.click(canvas, { clientX: 10, clientY: 10 });
    fireEvent.click(canvas, { clientX: 90, clientY: 10 });
    fireEvent.click(canvas, { clientX: 50, clientY: 90 });
    await user.click(screen.getByRole("button", { name: "영역 저장" }));

    expect(fetchMock).toHaveBeenCalledWith("/api/cameras/cam_001/zones", expect.objectContaining({ method: "PUT" }));
    const request = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(JSON.parse(String(request.body))).toEqual({ zones: [{ id: "restricted", type: "polygon", points: [{ x: 0.1, y: 0.1 }, { x: 0.9, y: 0.1 }, { x: 0.5, y: 0.9 }] }] });
  }, 10_000);

  it("allows keyboard users to add normalized coordinate points", async () => {
    const user = userEvent.setup();
    render(<CameraZoneEditor cameraId="cam_001" />);
    await user.click(screen.getByRole("button", { name: "영역 편집" }));

    const x = screen.getByLabelText("영역 점 X 좌표");
    const y = screen.getByLabelText("영역 점 Y 좌표");
    for (const [nextX, nextY] of [["0.1", "0.1"], ["0.9", "0.1"], ["0.5", "0.9"]]) {
      await user.clear(x);
      await user.type(x, nextX);
      await user.clear(y);
      await user.type(y, nextY);
      await user.click(screen.getByRole("button", { name: "점 추가" }));
    }

    expect(screen.getByRole("button", { name: "영역 저장" })).toBeEnabled();
  }, 10_000);

  it("saves a two-point line with its selected crossing direction", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: vi.fn().mockResolvedValue({}) } as unknown as Response);
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    const restricted = { id: "restricted", type: "polygon" as const, points: [{ x: 0.1, y: 0.1 }, { x: 0.9, y: 0.1 }, { x: 0.5, y: 0.9 }] };
    render(<CameraZoneEditor cameraId="cam_001" initialZones={[restricted]} />);

    await user.click(screen.getByRole("button", { name: "영역 편집" }));
    await user.click(screen.getByRole("button", { name: "통과 감지선" }));
    await user.selectOptions(screen.getByLabelText("허용 통과 방향"), "negative");

    const canvas = screen.getByRole("button", { name: "카메라 스냅샷 영역 편집" });
    vi.spyOn(canvas, "getBoundingClientRect").mockReturnValue({ left: 0, top: 0, width: 100, height: 100 } as DOMRect);
    fireEvent.click(canvas, { clientX: 10, clientY: 50 });
    fireEvent.click(canvas, { clientX: 90, clientY: 50 });
    expect(screen.getByTestId("line-direction-arrow")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "감지선 저장" }));

    const request = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(JSON.parse(String(request.body))).toEqual({
      zones: [restricted, { id: "line", type: "line", direction: "negative", points: [{ x: 0.1, y: 0.5 }, { x: 0.9, y: 0.5 }] }],
    });
  }, 10_000);

  it("saves a loitering polygon without replacing other zones", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: vi.fn().mockResolvedValue({ revision: 2 }) } as unknown as Response);
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    const restricted = { id: "restricted", type: "polygon" as const, points: [{ x: 0.1, y: 0.1 }, { x: 0.9, y: 0.1 }, { x: 0.5, y: 0.9 }] };
    const line = { id: "line", type: "line" as const, direction: "any" as const, points: [{ x: 0.1, y: 0.5 }, { x: 0.9, y: 0.5 }] };
    render(<CameraZoneEditor cameraId="cam_001" initialZones={[restricted, line]} />);
    await user.click(screen.getByRole("button", { name: "영역 편집" }));
    await user.selectOptions(screen.getByLabelText("영역 용도"), "loitering");
    const canvas = screen.getByRole("button", { name: "카메라 스냅샷 영역 편집" });
    vi.spyOn(canvas, "getBoundingClientRect").mockReturnValue({ left: 0, top: 0, width: 100, height: 100 } as DOMRect);
    fireEvent.click(canvas, { clientX: 10, clientY: 10 });
    fireEvent.click(canvas, { clientX: 90, clientY: 10 });
    fireEvent.click(canvas, { clientX: 50, clientY: 90 });
    await user.click(screen.getByRole("button", { name: "영역 저장" }));
    expect(JSON.parse(String((fetchMock.mock.calls[0]?.[1] as RequestInit).body))).toEqual({ zones: [restricted, line, { id: "loitering", type: "polygon", points: [{ x: 0.1, y: 0.1 }, { x: 0.9, y: 0.1 }, { x: 0.5, y: 0.9 }] }] });
  }, 10_000);

  it("shows, reloads, and deletes a saved zone", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: vi.fn().mockResolvedValue({ revision: 2 }) } as unknown as Response);
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    const restricted = { id: "restricted", type: "polygon" as const, points: [{ x: 0.1, y: 0.1 }, { x: 0.9, y: 0.1 }, { x: 0.5, y: 0.9 }] };
    const line = { id: "line", type: "line" as const, direction: "positive" as const, points: [{ x: 0.2, y: 0.5 }, { x: 0.8, y: 0.5 }] };
    render(<CameraZoneEditor cameraId="cam_001" initialZones={[restricted, line]} />);

    await user.click(screen.getByRole("button", { name: "영역 편집" }));
    expect(screen.getByTestId("saved-zone-line")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "통과 감지선 수정" }));
    expect(screen.getByLabelText("허용 통과 방향")).toHaveValue("positive");
    expect(screen.getByRole("button", { name: "감지선 저장" })).toBeEnabled();

    await user.click(screen.getByRole("button", { name: "통과 감지선 삭제" }));
    expect(JSON.parse(String((fetchMock.mock.calls[0]?.[1] as RequestInit).body))).toEqual({ zones: [restricted] });
    expect(await screen.findByText("통과 감지선을 삭제했습니다.")).toBeInTheDocument();
  });

  it("rejects a stale revision instead of overwriting newer zones", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 409,
      json: vi.fn().mockResolvedValue({ detail: "revision conflict" }),
    } as unknown as Response);
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    const restricted = { id: "restricted", type: "polygon" as const, points: [{ x: 0.1, y: 0.1 }, { x: 0.9, y: 0.1 }, { x: 0.5, y: 0.9 }] };
    render(<CameraZoneEditor cameraId="cam_001" initialZones={[restricted]} initialRevision={7} />);

    await user.click(screen.getByRole("button", { name: "영역 편집" }));
    await user.click(screen.getByRole("button", { name: "제한 구역 삭제" }));

    expect(JSON.parse(String((fetchMock.mock.calls[0]?.[1] as RequestInit).body))).toEqual({ zones: [], expected_revision: 7 });
    expect(await screen.findByRole("alert")).toHaveTextContent("최신 설정을 불러온 뒤 다시 시도");
    expect(screen.getByRole("button", { name: "최신 설정 불러오기" })).toBeInTheDocument();
  });
});
