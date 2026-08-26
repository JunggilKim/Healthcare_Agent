import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { readFile } from "node:fs/promises";

async function blockOutboundNetwork(page: import("@playwright/test").Page) {
  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (["127.0.0.1", "localhost"].includes(url.hostname)) await route.continue();
    else await route.abort("blockedbyclient");
  });
}

test("S004 frozen vertical slice works with outbound network blocked", async ({ page }) => {
  await blockOutboundNetwork(page);

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

  await page.getByRole("button", { name: "Researcher View" }).click();
  await expect(page.getByRole("heading", { name: /질문 효용 감사/ })).toBeVisible();
  const researchAccessibility = await new AxeBuilder({ page }).analyze();
  expect(
    researchAccessibility.violations.filter((item) => ["critical", "serious"].includes(item.impact ?? "")),
  ).toEqual([]);
  await expect(page).toHaveScreenshot("research-evidence-1440x900.png", { animations: "disabled" });

  await page.getByRole("button", { name: "Experiment Evidence" }).click();
  await expect(page.getByText(/Provisional fixture smoke/)).toBeVisible();
  const experimentAccessibility = await new AxeBuilder({ page }).analyze();
  expect(
    experimentAccessibility.violations.filter((item) => ["critical", "serious"].includes(item.impact ?? "")),
  ).toEqual([]);
  await expect(page).toHaveScreenshot("experiment-evidence-1440x900.png", { animations: "disabled" });
  await page.getByRole("button", { name: "Patient Summary" }).click();

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
  await blockOutboundNetwork(page);
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

test("export, reset, and delete preserve the session lifecycle", async ({ page }) => {
  await blockOutboundNetwork(page);
  await page.goto("/");
  await page.getByRole("button", { name: "S004 Snapshot 분석 시작" }).click();
  await expect(page.getByText("NCT05239624")).toBeVisible();
  const initialUrl = page.url();

  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export report" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/^trial-opt-.*\.json$/);
  const downloadPath = await download.path();
  expect(downloadPath).not.toBeNull();
  const exported = JSON.parse(await readFile(downloadPath!, "utf8")) as {
    report: { source: string };
    estimated_cost_usd: number;
    artifact_sha256: string;
  };
  expect(exported.report.source).toBe("DETERMINISTIC_TEMPLATE");
  expect(exported.estimated_cost_usd).toBe(0);
  expect(exported.artifact_sha256).toMatch(/^[a-f0-9]{64}$/);

  await page.getByRole("button", { name: "Reset session" }).click();
  await expect(page).not.toHaveURL(initialUrl);
  await expect(page.getByText("NCT05239624")).toBeVisible();
  await expect(page.getByRole("heading", { name: /병리검사 결과지/ })).toBeVisible();

  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Delete session" }).click();
  await expect(page.getByRole("heading", { name: /추측하지 않고/ })).toBeVisible();
  expect(await page.evaluate(() => Object.keys(sessionStorage).filter((key) => key.startsWith("trial-opt:")))).toEqual([]);
});

test("mobile Korean layout has no page overflow and no console errors", async ({ page }) => {
  const consoleProblems: string[] = [];
  page.on("console", (message) => {
    if (["error", "warning"].includes(message.type())) consoleProblems.push(`${message.type()}: ${message.text()}`);
  });
  page.on("pageerror", (error) => consoleProblems.push(`pageerror: ${error.message}`));
  await page.setViewportSize({ width: 390, height: 844 });
  await blockOutboundNetwork(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: /추측하지 않고/ })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await expect(page).toHaveScreenshot("landing-390x844.png", { animations: "disabled" });

  await page.getByRole("button", { name: "S004 Snapshot 분석 시작" }).click();
  await expect(page.getByText("NCT05239624")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await expect(page.getByRole("button", { name: "Replay Proof" })).toBeVisible();
  const accessibility = await new AxeBuilder({ page }).analyze();
  expect(
    accessibility.violations.filter((item) => ["critical", "serious"].includes(item.impact ?? "")),
  ).toEqual([]);
  await expect(page).toHaveScreenshot("s004-workspace-390x844.png", { animations: "disabled" });
  expect(consoleProblems).toEqual([]);
});

test("loading and API error states remain explicit", async ({ page }) => {
  await page.route("**/api/v1/sessions/*/analysis", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 800));
    await route.continue();
  });
  await page.goto("/");
  await page.getByRole("button", { name: "S004 Snapshot 분석 시작" }).click();
  await expect(page.getByRole("button", { name: "분석 중…" })).toBeVisible();
  await expect(page.getByText("NCT05239624")).toBeVisible();

  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Delete session" }).click();
  await expect(page.getByRole("heading", { name: /추측하지 않고/ })).toBeVisible();
  await page.route("**/api/v1/sessions", (route) =>
    route.fulfill({ status: 503, contentType: "application/json", body: '{"code":"TEST_UNAVAILABLE"}' }),
  );
  await page.getByRole("button", { name: "S004 Snapshot 분석 시작" }).click();
  await expect(page.getByRole("alert")).toContainText("API request failed: 503");
});
