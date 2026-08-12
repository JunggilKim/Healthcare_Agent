import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test("S004 frozen vertical slice works with outbound network blocked", async ({ page }) => {
  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (["127.0.0.1", "localhost"].includes(url.hostname)) await route.continue();
    else await route.abort("blockedbyclient");
  });

  await page.goto("/");
  await expect(page.getByRole("heading", { name: /추측하지 않고/ })).toBeVisible();
  await expect(page).toHaveScreenshot("landing-1440x900.png", { animations: "disabled" });
  await page.getByRole("button", { name: "S004 Snapshot 분석 시작" }).click();

  await expect(page.getByText("NCT05239624")).toBeVisible();
  await page.reload();
  await expect(page.getByText("NCT05239624")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Criterion proof table" })).toBeVisible();
  await expect(page.getByText("Pathology-confirmed urothelial histology")).toBeVisible();
  await expect(page.getByRole("heading", { name: /병리검사 결과지/ })).toBeVisible();
  await expect(page.getByText("Imaging suspicion ≠ pathology confirmation")).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "20 retained candidates · top 8 selected" }),
  ).toBeVisible();
  await expect(page.getByTestId("retrieval-candidate")).toHaveCount(20);
  await expect(page.getByText("opaque · review required").first()).toBeVisible();
  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(
    accessibility.violations.filter((item) => ["critical", "serious"].includes(item.impact ?? "")),
  ).toEqual([]);
  await expect(page).toHaveScreenshot("s004-workspace-1440x900.png", {
    animations: "disabled",
  });

  const ageRow = page.getByRole("row").filter({ hasText: "Age ≥ 18 years" });
  await expect(ageRow.getByText("PASS", { exact: true })).toBeVisible();
  const histologyRow = page
    .getByRole("row")
    .filter({ hasText: "Pathology-confirmed urothelial histology" });
  await expect(histologyRow.getByText("UNKNOWN", { exact: true })).toBeVisible();

  await page
    .getByRole("button", { name: /Pinned branch A · 병리기록에서 고등급 요로상피암 확인/ })
    .click();
  await expect(histologyRow.getByText("PASS", { exact: true })).toBeVisible();
  const invasionRow = page.getByRole("row").filter({ hasText: "Muscle-invasive disease" });
  await expect(invasionRow.getByText("UNKNOWN", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: /근육 침윤 여부/ })).toBeVisible();

  await page.getByRole("button", { name: "Replay Proof" }).click();
  await expect(page.getByText(/Proof replay passed · PV-012 7\/7/)).toBeVisible();
});

test("unknown and failure-rehearsal paths remain usable", async ({ page }) => {
  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (["127.0.0.1", "localhost"].includes(url.hostname)) await route.continue();
    else await route.abort("blockedbyclient");
  });
  await page.goto("/?demo-tools=1");
  await page.getByRole("button", { name: "S004 Snapshot 분석 시작" }).click();
  await page.getByRole("button", { name: "GEMINI_UNAVAILABLE" }).click();
  await expect(page.getByText(/Partial results preserved/)).toBeVisible();
  await page.getByRole("button", { name: "잘 모르겠습니다" }).click();
  await expect(page.getByRole("heading", { name: /근육 침윤 여부/ })).toBeVisible();
  await page.getByRole("button", { name: "이 기록을 제공할 수 없습니다" }).click();
  await expect(
    page.getByText("pathology.muscle_invasion unavailable · 동일 질문을 다시 묻지 않음"),
  ).toBeVisible();
  await expect(page.getByRole("heading", { name: /근육 침윤 여부/ })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Export report" })).toBeEnabled();
  await page.getByRole("button", { name: "Experiment Evidence" }).click();
  await expect(page.getByText(/Provisional fixture smoke/)).toBeVisible();
  await expect(page.getByText(/Acceptance eligible: false/)).toBeVisible();
});
