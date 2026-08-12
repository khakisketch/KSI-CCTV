import { expect, test, type Page } from "@playwright/test";

async function login(page: Page) {
  await page.goto("/login");
  await page.getByLabel("비밀번호").fill("e2e-web-password");
  await page.getByRole("button", { name: "로그인" }).click();
  await expect(page).toHaveURL(/\/$/);
}

test("onboards a camera and verifies a managed-camera operation", async ({ page }) => {
  await login(page);
  await page.goto("/cameras");
  await expect(page.getByRole("option", { name: /로비 카메라/ })).toBeVisible();

  await page.getByRole("link", { name: "관리" }).click();
  await expect(page).toHaveURL(/\/cameras\/cam-1$/);
  await page.getByRole("button", { name: "연결 검사" }).click();
  await expect(page.getByText("영상 연결 확인 완료: h264")).toBeVisible();
  await page.getByRole("button", { name: "활성화" }).click();
  await expect(page.getByText("변경 후 실행 상태까지 확인했습니다.")).toBeVisible();

  await page.goto("/cameras");
  await page.getByRole("button", { name: "카메라 추가" }).click();
  await page.getByRole("button", { name: /RTSP 주소 직접 입력/ }).click();
  await page.getByLabel("카메라 이름").fill("E2E 신규 카메라");
  await page.getByLabel("RTSP 주소").fill("rtsp://camera/live");
  await page.getByRole("button", { name: /다음/ }).click();
  await page.getByRole("button", { name: /다음/ }).click();
  await page.getByRole("button", { name: "스트림 검사 실행" }).click();
  await expect(page.getByText("연결 성공")).toBeVisible();
  await page.getByRole("button", { name: /다음/ }).click();
  await page.getByRole("button", { name: /다음/ }).click();
  await page.getByRole("button", { name: "등록 적용" }).click();
  await expect(page.getByText("카메라 운영 준비가 완료되었습니다")).toBeVisible();
});

test("filters event history, changes model controls, and records commissioning handoff", async ({ page }) => {
  await login(page);
  await page.goto("/events");
  await expect(page.getByRole("cell", { name: "출입구 사람 감지" })).toBeVisible();
  await page.locator("#main-content").getByLabel("이벤트 유형 필터").selectOption("fire");
  await expect(page.getByRole("cell", { name: "창고 화재 감지" })).toBeVisible();
  await expect(page.getByRole("cell", { name: "출입구 사람 감지" })).toHaveCount(0);
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "CSV 내보내기" }).click();
  expect((await downloadPromise).suggestedFilename()).toBe("events.csv");

  await page.goto("/models");
  await expect(page.getByText("open-vocab-default")).toBeVisible();
  await page.getByRole("button", { name: "활성화" }).click();
  await expect(page.getByText("활성화됨 (재시작 후 반영)")).toBeVisible();
  const prompts = page.locator("textarea");
  await prompts.fill("사람\n지게차");
  await page.getByRole("button", { name: "프롬프트 저장" }).click();
  await expect(page.getByText("프롬프트 2개 저장됨 (재시작 후 반영)")).toBeVisible();

  await page.goto("/commissioning");
  await expect(page.getByText("설치 진행률")).toBeVisible();
  await page.getByLabel("설치 메모").fill("E2E 설치 확인");
  await page.getByRole("button", { name: "확인 내용 저장" }).click();
  await expect(page.getByText("카메라 확인 내용을 저장했습니다.")).toBeVisible();
  await page.getByRole("button", { name: "설치 완료" }).click();
  await expect(page.getByText("설치 완료로 처리했습니다.")).toBeVisible();

  await page.goto("/handoff");
  await expect(page.getByText("E2E 검증 현장")).toBeVisible();
  await page.getByLabel("고객 담당자 이름").fill("E2E 고객 담당자");
  await page.getByLabel(/설치 결과와 미해결 항목/).check();
  await page.getByRole("button", { name: "인수 확인 기록" }).click();
  await expect(page.getByText("현장 인수 확인을 기록했습니다.")).toBeVisible();
});
