import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { GalleryView } from "@/components/gallery-view";
import { triggerIntersection } from "@/test/setup";
import type { CaptureDate } from "@/lib/engine";

const makeImages = (count: number, prefix: string) =>
  Array.from({ length: count }, (_, i) => ({
    name: `${prefix}-${String(i + 1).padStart(3, "0")}.jpg`,
    encrypted: false,
  }));

const DATES: CaptureDate[] = [
  {
    date: "20260617",
    cameras: [
      { camera_id: "cam-01", count: 20, images: makeImages(20, "cam01") },
      { camera_id: "cam-02", count: 30, images: makeImages(30, "cam02") },
    ],
  },
  {
    date: "20260616",
    cameras: [{ camera_id: "cam-01", count: 5, images: makeImages(5, "cam01") }],
  },
];

describe("GalleryView", () => {
  it("dates가 비어 있으면 안내 문구를 렌더한다", () => {
    render(<GalleryView dates={[]} />);

    expect(screen.getByText("저장된 캡처 이미지가 없습니다. 알람이 발생하면 여기에 표시됩니다.")).toBeInTheDocument();
  });

  it("처음에는 카메라 그룹별로 12장씩 이미지를 렌더한다", () => {
    render(<GalleryView dates={DATES} />);

    const images = screen.getAllByRole("img");
    expect(images).toHaveLength(24);
  });

  it("다중 카메라 시 두 번째 카메라도 표시된다", () => {
    render(<GalleryView dates={DATES} />);

    // 첫 번째 카메라 12장 + 두 번째 카메라 12장 = 24장
    expect(screen.getAllByRole("img")).toHaveLength(24);
    // 두 번째 카메라 썸네일이 실제로 존재하는지 확인
    expect(screen.getByAltText("cam02-001.jpg")).toBeInTheDocument();
  });

  it("첫 번째 카메라 sentinel이 뷰포트에 들어오면 해당 그룹만 12장씩 추가 이미지를 로드한다", async () => {
    render(<GalleryView dates={DATES} />);

    const sentinel = screen.getByTestId("gallery-sentinel-cam-01");
    triggerIntersection(sentinel, true);

    // cam-01 20장 모두 + cam-02 12장 = 32장
    await vi.waitFor(() => {
      expect(screen.getAllByRole("img")).toHaveLength(32);
    }, { timeout: 1000 });
  });

  it("두 번째 카메라 sentinel이 뷰포트에 들어오면 해당 그룹만 추가 이미지를 로드한다", async () => {
    render(<GalleryView dates={DATES} />);

    const sentinel = screen.getByTestId("gallery-sentinel-cam-02");
    triggerIntersection(sentinel, true);

    // cam-01 12장 + cam-02 24장 = 36장
    await vi.waitFor(() => {
      expect(screen.getAllByRole("img")).toHaveLength(36);
    }, { timeout: 1000 });
  });

  it("날짜를 변경하면 각 CameraGroup이 리마운트되어 visibleCount가 초기화된다", async () => {
    render(<GalleryView dates={DATES} />);

    // 먼저 cam-01 sentinel 트리거해 20장까지 로드
    const sentinel = screen.getByTestId("gallery-sentinel-cam-01");
    triggerIntersection(sentinel, true);
    await vi.waitFor(() => {
      expect(screen.getAllByRole("img")).toHaveLength(32);
    }, { timeout: 1000 });

    // 날짜 변경 시 각 CameraGroup이 새로 마운트되어 12장씩 초기화
    const nextDay = screen.getByRole("button", { name: "2026-06-16" });
    await userEvent.click(nextDay);

    const images = screen.getAllByRole("img");
    expect(images).toHaveLength(5);
  });
});
