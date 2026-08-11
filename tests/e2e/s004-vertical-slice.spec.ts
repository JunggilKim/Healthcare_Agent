import { expect, test } from "@playwright/test";

test("S004 frozen vertical slice works with outbound network blocked", async ({ page }) => {
  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (["127.0.0.1", "localhost"].includes(url.hostname)) await route.continue();
    else await route.abort("blockedbyclient");
  });

  await page.goto("/");
  await expect(page.getByRole("heading", { name: /추측하지 않고/ })).toBeVisible();
  await page.getByRole("button", { name: "S004 Snapshot 분석 시작" }).click();

  await expect(page.getByText("NCT05239624")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Criterion proof table" })).toBeVisible();
  await expect(page.getByText("Pathology-confirmed urothelial histology")).toBeVisible();
  await expect(page.getByRole("heading", { name: /병리검사 결과지/ })).toBeVisible();
  await expect(page.getByText("Imaging suspicion ≠ pathology confirmation")).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "20 retained candidates · top 8 selected" }),
  ).toBeVisible();
  await expect(page.getByTestId("retrieval-candidate")).toHaveCount(20);
  await expect(page.getByText("selected · not compiled").first()).toBeVisible();

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
  await expect(page.getByText("Proof replay passed · PV-012 7/7")).toBeVisible();
});
