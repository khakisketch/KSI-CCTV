import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import { ReportViewer } from "@/components/report-viewer";
import type { ReportDetail, ReportsList } from "@/lib/engine";

const list: ReportsList = {
  count: 1,
  reports: [
    {
      id: 3,
      period: "daily",
      since: "2026-09-21T00:00:00+00:00",
      until: "2026-09-22T00:00:00+00:00",
      model: "report-llm",
      created_at: "2026-09-22T00:00:00+00:00",
    },
  ],
  status: {
    enabled: false,
    live: false,
    retention_days: 90,
    periods: {
      daily: {
        enabled: true,
        last_run: null,
        last_success: "2026-09-22T00:00:00+00:00",
        next_run: "2026-09-23T00:00:00+00:00",
        failure_count: null,
      },
      weekly: {
        enabled: true,
        last_run: null,
        last_success: null,
        next_run: null,
        failure_count: null,
      },
    },
  },
};

const detail: ReportDetail = { ...list.reports[0], markdown: "# daily report\n\n- item\n" };

describe("ReportViewer", () => {
  it("스케줄 상태·목록·미선택 안내를 렌더한다", () => {
    render(<ReportViewer list={list} detail={null} selectedId={null} activePeriod={null} />);
    expect(screen.getByText("보고서 스케줄 상태")).toBeInTheDocument();
    expect(screen.getByText("스케줄 비활성")).toBeInTheDocument();
    expect(screen.getByText("스케줄러 미연결(저장소 기준 추정)")).toBeInTheDocument();
    expect(screen.getByText("#3")).toBeInTheDocument();
    expect(screen.getByText("목록에서 보고서를 선택하세요.")).toBeInTheDocument();
  });

  it("연속 실패 배지를 표시한다", () => {
    const failing: ReportsList = {
      ...list,
      status: {
        ...list.status!,
        periods: {
          ...list.status!.periods,
          weekly: { ...list.status!.periods.weekly, failure_count: 2 },
        },
      },
    };
    render(<ReportViewer list={failing} detail={null} selectedId={null} activePeriod={null} />);
    expect(screen.getByText("연속 실패 2")).toBeInTheDocument();
  });

  it("열람 markdown과 다운로드 버튼을 렌더하고 클릭 시 Blob 저장을 시도한다", () => {
    const originalUrl = globalThis.URL;
    const createUrl = vi.fn(() => "blob:mock");
    const revokeUrl = vi.fn();
    vi.stubGlobal("URL", { createObjectURL: createUrl, revokeObjectURL: revokeUrl });
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    try {
      render(<ReportViewer list={list} detail={detail} selectedId={3} activePeriod={null} />);
      expect(screen.getByText("# daily report", { exact: false })).toBeInTheDocument();
      screen.getByText("Markdown 다운로드").click();
      expect(createUrl).toHaveBeenCalledOnce();
      const anchor = clickSpy.mock.instances[0] as unknown as HTMLAnchorElement;
      expect(anchor.download).toBe("report-3-daily-20260922.md");
    } finally {
      clickSpy.mockRestore();
      // setup.ts의 전역 shim(IntersectionObserver 등)을 건드리지 않고 URL만 원복한다.
      vi.stubGlobal("URL", originalUrl);
    }
  });

  it("빈 목록 메시지를 렌더한다", () => {
    render(
      <ReportViewer list={{ ...list, count: 0, reports: [] }} detail={null} selectedId={null} activePeriod="weekly" />,
    );
    expect(screen.getByText("표시할 보고서가 없습니다.")).toBeInTheDocument();
  });
});
