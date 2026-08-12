import { expect, test } from "@playwright/test";

test("requires login, renders the protected console, and logs out", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByText("KSI-CCTV 로그인")).toBeVisible();

  await page.getByLabel("비밀번호").fill("wrong-password");
  await page.getByRole("button", { name: "로그인" }).click();
  await expect(page.getByText("비밀번호가 올바르지 않습니다.")).toBeVisible();

  await page.getByLabel("비밀번호").fill("e2e-web-password");
  await page.getByRole("button", { name: "로그인" }).click();
  await expect(page).toHaveURL(/\/$/);

  await expect(page.getByRole("heading", { name: "창고 화재 감지" })).toBeVisible();
  await expect(page.getByRole("listbox", { name: "사건 목록" })).toBeVisible();

  await page.getByRole("button", { name: "사건 확인" }).click();
  await expect(page.getByText("사건을 확인 상태로 기록했습니다.")).toBeVisible();
  await expect(page.getByRole("button", { name: "대응 시작" })).toBeVisible();

  await page.getByRole("link", { name: "라이브", exact: true }).click();
  await expect(page).toHaveURL(/\/live$/);

  await page.getByRole("link", { name: "Signal" }).click();
  await expect(page).toHaveURL(/\/$/);

  await page.getByRole("button", { name: "로그아웃" }).click();
  await expect(page).toHaveURL(/\/login$/);
});
