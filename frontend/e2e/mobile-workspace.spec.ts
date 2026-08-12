import { expect, test, type Page } from "@playwright/test";

async function login(page: Page) {
  await page.goto("/login");
  await page.getByLabel("비밀번호").fill("e2e-web-password");
  await page.getByRole("button", { name: "로그인" }).click();
  await expect(page).toHaveURL(/\/$/);
}

test("keeps camera management and event detail reachable on a phone viewport", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page);

  await page.goto("/cameras");
  const manage = page.getByRole("link", { name: "관리" });
  await expect(manage).toBeVisible();
  await manage.click();
  await expect(page).toHaveURL(/\/cameras\/cam-1$/);

  await page.goto("/events");
  await page.getByRole("cell", { name: "창고 화재 감지" }).click();
  await expect(page.getByRole("link", { name: "라이브 보기" })).toBeVisible();
  await expect(page.getByRole("link", { name: "사건 상세 및 처리" })).toBeVisible();
});
