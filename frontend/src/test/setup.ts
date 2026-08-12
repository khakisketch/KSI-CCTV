import "@testing-library/jest-dom/vitest";
import { afterEach, vi } from "vitest";
import { act, cleanup } from "@testing-library/react";

// jsdom에는 IntersectionObserver가 없으므로 mock 제공.
// observe() 호출 시 기본적으로 isIntersecting=false로 반환해 무한스크롤이
// 자동으로 이미지를 늘리지 않도록 한다. 필요한 테스트에서 개별적으로 trigger()로 제어.
const observers = new Map<Element, IntersectionObserverCallback>();

const mockIntersectionObserver = vi.fn((callback: IntersectionObserverCallback) => ({
  disconnect: vi.fn(() => {
    observers.forEach((cb, el) => {
      if (cb === callback) observers.delete(el);
    });
  }),
  observe: vi.fn((el: Element) => {
    observers.set(el, callback);
    callback(
      [
        {
          isIntersecting: false,
          target: el,
          boundingClientRect: {} as DOMRectReadOnly,
          intersectionRatio: 0,
          intersectionRect: {} as DOMRectReadOnly,
          rootBounds: null,
          time: Date.now(),
        },
      ] as IntersectionObserverEntry[],
      mockIntersectionObserver as unknown as IntersectionObserver,
    );
  }),
  unobserve: vi.fn((el: Element) => observers.delete(el)),
  takeRecords: vi.fn(),
}));

vi.stubGlobal("IntersectionObserver", mockIntersectionObserver);

export function triggerIntersection(target: Element, isIntersecting = true) {
  const cb = observers.get(target);
  if (!cb) return;
  act(() => {
    cb(
      [
        {
          isIntersecting,
          target,
          boundingClientRect: {} as DOMRectReadOnly,
          intersectionRatio: isIntersecting ? 1 : 0,
          intersectionRect: {} as DOMRectReadOnly,
          rootBounds: null,
          time: Date.now(),
        },
      ] as IntersectionObserverEntry[],
      mockIntersectionObserver as unknown as IntersectionObserver,
    );
  });
}

afterEach(() => {
  observers.clear();
  cleanup();
});
