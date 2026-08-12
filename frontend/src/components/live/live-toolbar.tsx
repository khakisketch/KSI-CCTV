"use client";

import { ChevronLeft, ChevronRight, Expand, Grid2x2, Grid3x3, Minimize, Rows2, RotateCcw, Square } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { LiveLayout, LiveSort } from "@/components/live/use-live-presets";

interface LiveToolbarProps {
  layout: LiveLayout;
  onLayoutChange: (l: LiveLayout) => void;
  sort: LiveSort;
  onSortChange: (s: LiveSort) => void;
  autoPlayCount: number;
  onAutoPlayCountChange: (n: number) => void;
  cameraCount: number;
  maxAutoPlay: number;
  fullscreen: boolean;
  onToggleFullscreen: () => void;
  focusCameraId: string | null;
  onExitFocus: () => void;
  hasCustomOrder: boolean;
  onResetOrder: () => void;
  page: number;
  pageCount: number;
  onPageChange: (page: number) => void;
}

const LAYOUTS: { value: LiveLayout; icon: typeof Grid2x2; label: string }[] = [
  { value: 1, icon: Square, label: "1" },
  { value: 4, icon: Grid2x2, label: "4" },
  { value: 9, icon: Grid3x3, label: "9" },
  { value: 16, icon: Rows2, label: "16" },
];

const SORTS: { value: LiveSort; label: string }[] = [
  { value: "risk", label: "위험도 우선" },
  { value: "healthy", label: "정상 수신 우선" },
  { value: "name", label: "이름순" },
  { value: "default", label: "기본 순서" },
];

export function LiveToolbar({
  layout,
  onLayoutChange,
  sort,
  onSortChange,
  autoPlayCount,
  onAutoPlayCountChange,
  cameraCount,
  maxAutoPlay,
  fullscreen,
  onToggleFullscreen,
  focusCameraId,
  onExitFocus,
  hasCustomOrder,
  onResetOrder,
  page,
  pageCount,
  onPageChange,
}: LiveToolbarProps) {
  const autoPlayOptions = Array.from({ length: Math.min(16, Math.max(1, cameraCount)) }, (_, i) => i + 1);
  const canDragReorder = sort === "default" && cameraCount > 1;

  return (
    <div
      className="flex flex-wrap items-center gap-2 rounded-lg border border-border bg-background/80 p-2 backdrop-blur supports-[backdrop-filter]:bg-background/60 live-toolbar"
      data-toolbar
    >
      {/* 레이아웃 세그먼트 */}
      <div className="inline-flex items-center gap-0.5 rounded-md border border-border p-0.5">
        {LAYOUTS.map(({ value, icon: Icon, label }) => (
          <button
            key={value}
            type="button"
            onClick={() => onLayoutChange(value)}
            aria-pressed={layout === value}
            aria-label={`${value}분할`}
            title={`${value}분할`}
            className={cn(
              "inline-flex size-11 items-center justify-center rounded text-xs transition sm:size-7",
              layout === value
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:bg-muted hover:text-foreground",
            )}
          >
            <Icon className="size-3.5" />
            <span className="sr-only">{label}</span>
          </button>
        ))}
      </div>

      {/* 정렬 */}
      <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
        정렬
        <select
          value={sort}
          onChange={(e) => onSortChange(e.target.value as LiveSort)}
          className="h-7 rounded border border-border bg-background px-1.5 text-xs text-foreground"
        >
          {SORTS.map((s) => (
            <option key={s.value} value={s.value}>
              {s.label}
            </option>
          ))}
        </select>
      </label>

      {/* 드래그 순서 안내 + 초기화: 정렬이 '기본 순서'일 때만 사용자가 타일을 드래그해
          사용자 순서(presets.order)를 만들 수 있다. 정렬 옵션을 바꾸면 DnD가 비활성. */}
      {canDragReorder ? (
        <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <span aria-hidden>↔</span>
          <span style={{ wordBreak: "keep-all" }}>타일을 드래그해 순서 변경 가능</span>
          {hasCustomOrder && (
            <Button
              size="icon-xs"
              variant="ghost"
              aria-label="사용자 순서 초기화"
              title="사용자가 지정한 순서를 지우고 등록 순으로 되돌립니다"
              onClick={onResetOrder}
              className="text-muted-foreground hover:text-foreground"
            >
              <RotateCcw className="size-3" />
            </Button>
          )}
        </div>
      ) : (
        hasCustomOrder && (
          <Button
            size="xs"
            variant="ghost"
            aria-label="사용자 순서 초기화"
            title="사용자가 지정한 순서를 지우고 등록 순으로 되돌립니다"
            onClick={onResetOrder}
            className="text-muted-foreground hover:text-foreground"
          >
            <RotateCcw className="size-3" />
            순서 초기화
          </Button>
        )
      )}

      {/* 자동 재생 수 */}
      <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
        자동 재생
        <select
          value={autoPlayCount}
          onChange={(e) => onAutoPlayCountChange(Number(e.target.value))}
          className="h-7 rounded border border-border bg-background px-1.5 text-xs text-foreground"
        >
          {autoPlayOptions.map((n) => (
            <option key={n} value={n}>
              {n <= maxAutoPlay ? n : `${n} (최대 ${maxAutoPlay})`}
            </option>
          ))}
        </select>
        <span className="tabular-nums">/ {cameraCount}</span>
      </label>

      {/* 페이지네이션: 레이아웃 용량보다 카메라가 많을 때만 노출 */}
      {pageCount > 1 && (
        <div className="flex items-center gap-1 text-xs text-muted-foreground">
          <Button
            size="icon-xs"
            variant="ghost"
            aria-label="이전 페이지"
            disabled={page <= 0}
            onClick={() => onPageChange(page - 1)}
            className="text-muted-foreground hover:text-foreground"
          >
            <ChevronLeft className="size-3" />
          </Button>
          <span className="tabular-nums" aria-live="polite">
            {page + 1} / {pageCount}
          </span>
          <Button
            size="icon-xs"
            variant="ghost"
            aria-label="다음 페이지"
            disabled={page >= pageCount - 1}
            onClick={() => onPageChange(page + 1)}
            className="text-muted-foreground hover:text-foreground"
          >
            <ChevronRight className="size-3" />
          </Button>
        </div>
      )}

      <div className="flex-1" />

      {/* 포커스 종료 */}
      {focusCameraId && (
        <Button size="sm" variant="secondary" onClick={onExitFocus}>
          그리드로 돌아가기
        </Button>
      )}

      {/* 전체 화면 */}
      <Button
        size="sm"
        variant="outline"
        onClick={onToggleFullscreen}
        aria-label={fullscreen ? "전체 화면 종료" : "관제 모드 (전체 화면)"}
        title={fullscreen ? "전체 화면 종료" : "관제 모드 (전체 화면)"}
      >
        {fullscreen ? <Minimize className="size-3.5" /> : <Expand className="size-3.5" />}
        {fullscreen ? "전체화면 종료" : "관제 모드"}
      </Button>
    </div>
  );
}
