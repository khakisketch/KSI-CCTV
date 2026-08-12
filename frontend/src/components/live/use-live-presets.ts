"use client";

import { useCallback, useEffect, useState, useSyncExternalStore } from "react";

export type LiveLayout = 1 | 4 | 9 | 16;
export type LiveSort = "default" | "risk" | "healthy" | "name";

export interface LivePresets {
  layout: LiveLayout;
  order: string[];
  autoPlayCount: number;
  sort: LiveSort;
  focusCameraId: string | null;
}

const DEFAULT_PRESETS: LivePresets = {
  layout: 4,
  order: [],
  autoPlayCount: 4,
  sort: "risk",
  focusCameraId: null,
};

const STORAGE_PREFIX = "ksi-cctv:live:presets";

function storageKey(siteKey: string): string {
  return `${STORAGE_PREFIX}:${siteKey}`;
}

function siteKeyFrom(site: string | undefined | null): string {
  // 슬래시/콜론 등 localStorage 키로 부적절한 문자를 치환한다. 빈 값이면 "default".
  const trimmed = (site ?? "").trim();
  return (trimmed || "default").replace(/[^A-Za-z0-9._-]+/g, "-");
}

function validLayout(v: unknown): v is LiveLayout {
  return v === 1 || v === 4 || v === 9 || v === 16;
}

function validSort(v: unknown): v is LiveSort {
  return v === "default" || v === "risk" || v === "healthy" || v === "name";
}

function coercePresets(raw: Partial<LivePresets> | null | undefined): LivePresets {
  if (!raw) return DEFAULT_PRESETS;
  return {
    ...DEFAULT_PRESETS,
    ...raw,
    layout: validLayout(raw.layout) ? raw.layout! : DEFAULT_PRESETS.layout,
    autoPlayCount:
      typeof raw.autoPlayCount === "number" && raw.autoPlayCount >= 0 && raw.autoPlayCount <= 16
        ? raw.autoPlayCount
        : DEFAULT_PRESETS.autoPlayCount,
    sort: validSort(raw.sort) ? raw.sort! : DEFAULT_PRESETS.sort,
    focusCameraId: null, // 포커스 모드는 세션 상태이므로 영속화하지 않는다.
  };
}

// ── snapshot 캐시 ────────────────────────────────────────────────────────
// useSyncExternalStore의 getSnapshot은 identity-stable이어야 한다. 매번 새 객체를
// 반환하면 React가 무한 렌더에 빠진다. localStorage 원본 문자열과 siteKey를 키로
// 사용해 파싱 결과를 캐싱한다.
interface SnapshotCacheEntry {
  siteKey: string;
  raw: string | null;
  value: LivePresets;
}
let snapshotCache: SnapshotCacheEntry | null = null;

function readSnapshot(siteKey: string): LivePresets {
  if (typeof window === "undefined") return DEFAULT_PRESETS;
  let raw: string | null = null;
  try {
    raw = window.localStorage.getItem(storageKey(siteKey));
  } catch {
    raw = null;
  }
  // 캐시 히트 시 동일 객체 반환.
  if (snapshotCache && snapshotCache.siteKey === siteKey && snapshotCache.raw === raw) {
    return snapshotCache.value;
  }
  let parsed: Partial<LivePresets> | null = null;
  try {
    parsed = raw ? (JSON.parse(raw) as Partial<LivePresets>) : null;
  } catch {
    // 손상된 localStorage 값은 기본값으로 안전하게 복구한다.
    parsed = null;
  }
  const value = coercePresets(parsed);
  snapshotCache = { siteKey, raw, value };
  return value;
}

function invalidateSnapshot(siteKey: string) {
  if (snapshotCache && snapshotCache.siteKey === siteKey) snapshotCache = null;
}

// 다중 탭/윈도우에서 같은 siteKey 변경을 알리기 위한 인-메모리 구독 채널.
type Listener = (changedKey: string) => void;
const listeners = new Set<Listener>();

// 현장(설치 사이트)별로 라이브 월 레이아웃/정렬/자동재생 수를 localStorage에 저장한다.
// 동일 설치자가 여러 관제 모니터를 쓸 때 동일한 정렬 조건을 유지하기 위함.
export function useLivePresets(site: string | undefined | null) {
  // site 변경(설치 현장 전환) 시 새로운 key를 즉시 구독하도록 render마다 파생한다.
  // useState 초기값으로 고정하면 이전 현장의 preset을 읽고 쓰는 버그가 발생한다.
  const siteKey = siteKeyFrom(site);

  // useSyncExternalStore: 외부 저장소(localStorage) 구독을 React가 인식하는 공식 패턴.
  //   - getSnapshot: 캐시된 객체 반환(새 객체 X — 무한 렌더 방지)
  //   - getServerSnapshot: SSR/최초 hydration 시점에는 DEFAULT 반환(mismatch 방지)
  //   - subscribe: storage 이벤트 + 인-메모리 set 으로 갱신 알림(같은 탭 내 쓰기 포함)
  const subscribeFn = useCallback((onStoreChange: () => void): (() => void) => {
    const key = storageKey(siteKey);
    const listener: Listener = (changedKey) => {
      if (!changedKey || changedKey === key) {
        invalidateSnapshot(siteKey);
        onStoreChange();
      }
    };
    listeners.add(listener);
    const onStorage = (e: StorageEvent) => listener(e.key ?? "");
    if (typeof window !== "undefined") window.addEventListener("storage", onStorage);
    return () => {
      listeners.delete(listener);
      if (typeof window !== "undefined") window.removeEventListener("storage", onStorage);
    };
  }, [siteKey]);

  const getSnapshot = useCallback(() => readSnapshot(siteKey), [siteKey]);
  const getServerSnapshot = useCallback(() => DEFAULT_PRESETS, []);

  const presets = useSyncExternalStore(subscribeFn, getSnapshot, getServerSnapshot);
  const hydrated = useHydrated();

  // 쓰기: localStorage 갱신 후 스냅샷 캐시 무효화 + 인-메모리 구독자에게 알림.
  const update = useCallback((patch: Partial<LivePresets>) => {
    if (typeof window === "undefined") return;
    const key = storageKey(siteKey);
    const next = { ...readSnapshot(siteKey), ...patch };
    try {
      window.localStorage.setItem(key, JSON.stringify(next));
    } catch {
      // 쓰기 실패(할당량 초과/프라이빗 모드) — 메모리 상태로 계속 동작.
    }
    invalidateSnapshot(siteKey);
    for (const cb of listeners) cb(key);
  }, [siteKey]);

  return { presets, setPresets: update, hydrated } as const;
}

// hydration 완료 여부 단순 감지. 첫 클라이언트 페인트 이후 true.
function useHydrated(): boolean {
  const [hydrated, setHydrated] = useState(false);
  useEffect(() => {
    // setState-in-effect lint 회피: useEffect 종료 신호용으로 한 번만 변경.
    // 의미상 "클라이언트에서 마운트 완료" 플래그이고 빈번히 호출되지 않는다.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setHydrated(true);
  }, []);
  return hydrated;
}
