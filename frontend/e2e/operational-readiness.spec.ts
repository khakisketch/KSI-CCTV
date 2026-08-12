import { expect, test, type Page } from "@playwright/test";

async function login(page: Page) {
  await page.goto("/login");
  await page.getByLabel("비밀번호").fill("e2e-web-password");
  await page.getByRole("button", { name: "로그인" }).click();
  await expect(page).toHaveURL(/\/$/);
}

test("shows diagnostic remediation and structured audit details", async ({ page }) => {
  await login(page);

  await page.goto("/diagnostics");
  const gpuCheck = page.getByRole("row", { name: /GPU 실행 공급자/ });
  await expect(gpuCheck).toBeVisible();
  await gpuCheck.click();
  await expect(page.getByText("GPU overlay와 CUDA 공급자를 확인하세요.", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "다시 확인" }).click();
  await expect(page.getByRole("status")).toHaveText("진단 결과를 갱신했습니다.");

  await page.goto("/audit");
  const pipelineEntry = page.getByRole("row", { name: /pipeline.started/ });
  await expect(pipelineEntry).toBeVisible();
  await pipelineEntry.click();
  await expect(page.locator("dt", { hasText: "options" }).first()).toBeVisible();
  await expect(page.locator("pre", { hasText: '"mode": "policy"' }).first()).toBeVisible();
});
