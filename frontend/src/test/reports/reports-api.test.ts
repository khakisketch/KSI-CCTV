import { describe, expect, it } from "vitest";

import {
  buildReportsQuery,
  formatReportPeriod,
  parseReportDetail,
  parseReportsList,
  parseReportsStatus,
  reportDownloadFilename,
  type ReportSummary,
} from "@/lib/engine";
import { minRoleForPath } from "@/lib/roles";

const summary: ReportSummary = {
  id: 3,
  period: "daily",
  since: "2026-09-21T00:00:00+00:00",
  until: "2026-09-22T00:00:00+00:00",
  model: "report-llm",
  created_at: "2026-09-22T00:00:00+00:00",
};

const status = {
  enabled: true,
  live: true,
  retention_days: 90,
  periods: {
    daily: {
      enabled: true,
      last_run: "2026-09-22T00:00:00+00:00",
      last_success: "2026-09-22T00:00:00+00:00",
      next_run: "2026-09-23T00:00:00+00:00",
      failure_count: 0,
    },
    weekly: {
      enabled: true,
      last_run: null,
      last_success: null,
      next_run: null,
      failure_count: 2,
    },
  },
};

describe("reports engine helpers", () => {
  it("목록 파싱 — 메타데이터·상태를 그대로 전달한다", () => {
    const parsed = parseReportsList({ count: 1, reports: [summary], status });
    expect(parsed?.count).toBe(1);
    expect(parsed?.reports[0]).toEqual(summary);
    expect(parsed?.status?.periods.daily.failure_count).toBe(0);
    expect(parsed?.status?.periods.weekly.failure_count).toBe(2);
  });

  it("목록 파싱 — 여분 키는 무시하고 이상 period를 거부한다", () => {
    expect(parseReportsList({ count: 1, reports: [{ ...summary, markdown: "# x" }], status })?.count).toBe(1);
    expect(parseReportsList({ count: 1, reports: [{ ...summary, period: "monthly" }], status })).toBeNull();
    expect(parseReportsList({ count: 1, reports: [summary] })).not.toBeNull();
    expect(parseReportsList(null)).toBeNull();
  });

  it("열람 파싱 — markdown 없으면 null", () => {
    expect(parseReportDetail({ report: { ...summary, markdown: "# daily\n" } })?.markdown).toBe("# daily\n");
    expect(parseReportDetail({ report: summary })).toBeNull();
    expect(parseReportDetail({ report: null })).toBeNull();
  });

  it("상태 파싱 — 주기 키 누락 시 null", () => {
    expect(parseReportsStatus(status)).not.toBeNull();
    expect(parseReportsStatus({ ...status, periods: { daily: status.periods.daily } })).toBeNull();
  });

  it("다운로드 파일명 — id·주기·날짜를 포함한다", () => {
    expect(reportDownloadFilename(summary)).toBe("report-3-daily-20260922.md");
    expect(reportDownloadFilename({ ...summary, created_at: "broken" })).toBe("report-3-daily-nodate.md");
  });

  it("주기 한글 표기", () => {
    expect(formatReportPeriod("daily")).toBe("일간");
    expect(formatReportPeriod("weekly")).toBe("주간");
  });

  it("목록 쿼리 — 비정상 limit은 생략하고 상한을 clamp한다 (M-3)", () => {
    expect(buildReportsQuery({})).toBe("");
    expect(buildReportsQuery({ limit: 50 })).toBe("?limit=50");
    expect(buildReportsQuery({ limit: 0 })).toBe("");
    expect(buildReportsQuery({ limit: -5 })).toBe("");
    expect(buildReportsQuery({ limit: Number.NaN })).toBe("");
    expect(buildReportsQuery({ limit: 500 })).toBe("?limit=200");
    expect(buildReportsQuery({ limit: 10, period: "weekly" })).toBe("?limit=10&period=weekly");
  });

  it("RBAC — 보고서 조회는 viewer, 변경은 fail-closed(admin)", () => {
    expect(minRoleForPath("/api/reports", "GET")).toBe("viewer");
    expect(minRoleForPath("/api/reports/3", "GET")).toBe("viewer");
    expect(minRoleForPath("/api/reports/3/download", "GET")).toBe("viewer");
    expect(minRoleForPath("/api/reports", "POST")).toBe("admin");
    expect(minRoleForPath("/api/reports/3", "DELETE")).toBe("admin");
  });
});
