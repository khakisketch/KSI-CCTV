import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";

import { EngineStatus } from "@/components/engine-status";

describe("EngineStatus", () => {
  it("connected=true일 때 '엔진 연결됨' 텍스트와 emerald 클래스를 렌더한다", () => {
    const { container } = render(<EngineStatus connected={true} />);

    expect(screen.getByText("엔진 연결됨")).toBeInTheDocument();
    const dot = container.querySelector(".bg-emerald-500");
    expect(dot).toBeInTheDocument();
  });

  it("connected=false일 때 '엔진 API 미연결' 텍스트와 amber 클래스를 렌더한다", () => {
    const { container } = render(<EngineStatus connected={false} />);

    expect(screen.getByText("엔진 API 미연결")).toBeInTheDocument();
    const dot = container.querySelector(".bg-amber-500");
    expect(dot).toBeInTheDocument();
  });
});
