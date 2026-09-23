import { expect, test, type Page } from "@playwright/test";

async function login(page: Page) {
  await page.goto("/login");
  await page.getByLabel("비밀번호").fill("e2e-web-password");
  await page.getByRole("button", { name: "로그인" }).click();
  await expect(page).toHaveURL(/\/$/);
}

test.beforeEach(async ({ request }) => {
  await request.post("http://127.0.0.1:4176/__reset");
});

test.afterEach(async ({ request }) => {
  await request.post("http://127.0.0.1:4176/__reset");
});

test("takes an incident from acknowledgement through dispositioned resolution", async ({ page, request }) => {
  const malformed = await request.post("http://127.0.0.1:4176/api/incidents/evt-2/actions", {
    data: { status: "invalid" },
  });
  expect(malformed.status()).toBe(422);
  const unchanged = await request.get("http://127.0.0.1:4176/api/incidents/evt-2");
  expect((await unchanged.json()).incident.status).toBe("new");

  await login(page);
  await page.goto("/incidents/evt-2");

  await page.getByLabel("조치 메모").fill("현장 확인 시작");
  await page.getByRole("button", { name: "확인", exact: true }).click();
  await expect(page.getByText("확인", { exact: true }).first()).toBeVisible();
  await page.getByRole("button", { name: "대응 시작" }).click();
  await expect(page.getByText("대응 중", { exact: true }).first()).toBeVisible();
  await page.getByLabel("종결 판정").selectOption("false_positive");
  await page.getByLabel("조치 메모").fill("오탐으로 종결");
  await page.getByRole("button", { name: "종결" }).click();

  await expect(page.getByText("종결", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("판정: false_positive")).toBeVisible();
  const history = page.getByRole("list", { name: "조치 이력" });
  await expect(history).toContainText("확인 · 현장 확인 시작");
  await expect(history).toContainText("대응 중");
  await expect(history).toContainText("종결 · false_positive · 오탐으로 종결");
});

test("recovers a stale camera revision and saves a normalized line zone", async ({ page }) => {
  await login(page);
  await page.goto("/cameras/cam-1");
  await page.getByRole("button", { name: "영역 편집" }).click();
  await expect(page.getByRole("img", { name: "카메라 스냅샷" })).toBeVisible();
  await page.getByRole("button", { name: "통과 감지선" }).click();
  await page.getByLabel("허용 통과 방향").selectOption("positive");
  await page.getByLabel("영역 점 X 좌표").fill("0.2");
  await page.getByLabel("영역 점 Y 좌표").fill("0.3");
  await page.getByRole("button", { name: "점 추가" }).click();
  await page.getByLabel("영역 점 X 좌표").fill("0.8");
  await page.getByLabel("영역 점 Y 좌표").fill("0.7");
  await page.getByRole("button", { name: "점 추가" }).click();
  await page.getByRole("button", { name: "감지선 저장" }).click();
  await expect(page.getByText("다른 작업에서 카메라 설정이 변경되었습니다. 최신 설정을 불러온 뒤 다시 시도하세요.")).toBeVisible();
  await page.getByRole("button", { name: "최신 설정 불러오기" }).click();
  await expect(page.getByRole("button", { name: "영역 편집" })).toBeVisible();

  await page.getByRole("button", { name: "영역 편집" }).click();
  await page.getByRole("button", { name: "통과 감지선" }).click();
  await page.getByLabel("허용 통과 방향").selectOption("positive");
  await page.getByLabel("영역 점 X 좌표").fill("0.2");
  await page.getByLabel("영역 점 Y 좌표").fill("0.3");
  await page.getByRole("button", { name: "점 추가" }).click();
  await page.getByLabel("영역 점 X 좌표").fill("0.8");
  await page.getByLabel("영역 점 Y 좌표").fill("0.7");
  await page.getByRole("button", { name: "점 추가" }).click();
  await page.getByRole("button", { name: "감지선 저장" }).click();
  await expect(page.getByRole("status")).toHaveText("통과 감지선을 저장했습니다.");
  await expect(page.getByText("통과 감지선 · 2개 점 · positive")).toBeVisible();
});

test("saves alert preferences and downloads the support bundle", async ({ page }) => {
  await login(page);
  await page.goto("/settings");
  await page.getByRole("button", { name: "critical" }).click();
  await page.getByRole("checkbox", { name: "person high", exact: true }).uncheck();
  await page.getByRole("button", { name: "알림 설정 저장" }).click();
  await expect(page.getByText("알림 설정을 저장했습니다 (파이프라인 재시작 후 적용).")).toBeVisible();

  await page.goto("/diagnostics");
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("link", { name: "지원 자료" }).click();
  expect((await downloadPromise).suggestedFilename()).toBe("ksi-cctv-support.zip");
  const bundle = await page.evaluate(async () => {
    const response = await fetch("/api/support/bundle");
    return { type: response.headers.get("content-type"), bytes: (await response.arrayBuffer()).byteLength };
  });
  expect(bundle).toEqual({ type: "application/zip", bytes: 22 });
});

test("validates an uploaded ONNX fixture and leaves it disabled", async ({ page }) => {
  await login(page);
  await page.goto("/models");
  await page.getByLabel("ONNX 파일").setInputFiles({
    name: "e2e-safety-model.onnx",
    mimeType: "application/octet-stream",
    buffer: Buffer.from("not-a-real-onnx"),
  });
  await page.getByLabel("모델 표시 이름").fill("e2e-safety-model");
  await page.getByRole("button", { name: "업로드 후 검증" }).click();
  await expect(page.getByText("업로드·검증 완료: e2e-safety-model (비활성 등록)")).toBeVisible();
  await expect(page.getByText("e2e-safety-model", { exact: true })).toBeVisible();
  await expect(page.getByText("설정 비활성", { exact: true }).last()).toBeVisible();
});
