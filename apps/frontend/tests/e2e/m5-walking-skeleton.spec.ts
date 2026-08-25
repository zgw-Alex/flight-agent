import { expect, test } from "@playwright/test";

test("E2E-01 publishes the golden Beijing to Shanghai recommendation", async ({ page }) => {
  await page.goto("/");

  await page.getByLabel("Origin").fill("PEK");
  await page.getByLabel("Destination").fill("SHA");
  await page.getByLabel("Departure date").fill("2026-09-01");
  await page.getByLabel("Max price").fill("1200");
  await page.getByRole("button", { name: "Search" }).click();

  const result = page.getByRole("region", { name: "Conversation result" });
  await expect(result.getByRole("heading", { name: "PUBLISHED" })).toBeVisible();
  await expect(result).toContainText("PEK to SHA");
  await expect(result).toContainText("2026-09-01");
  await expect(result).toContainText("CNY 980");
  await expect(result).toContainText("BEST_OVERALL");
  await expect(result).toContainText("Selected from rank 1 lower-price result");
  await expect(result).toContainText("OFFER:");
  await expect(result).toContainText("Requirement version");
  await expect(result).toContainText("Publication id");
  await expect(result).toContainText("Snapshot id");
  await expect(result).not.toContainText("filter_result");
  await expect(result).not.toContainText("ranking_result");
  await expect(result).not.toContainText("ProviderRawEvidence");
});

test("E2E-02 renders provider failure without a fake publication", async ({ page }) => {
  await page.goto("/");

  await page.getByLabel("Origin").fill("SHA");
  await page.getByLabel("Destination").fill("LAX");
  await page.getByLabel("Departure date").fill("2026-09-01");
  await page.getByLabel("Max price").fill("1200");
  await page.getByRole("button", { name: "Search" }).click();

  const result = page.getByRole("region", { name: "Conversation result" });
  await expect(result.getByRole("heading", { name: "PROVIDER_ERROR" })).toBeVisible();
  await expect(result).toContainText("The flight provider did not return a usable search result.");
  await expect(result).not.toContainText("PUBLISHED");
  await expect(result).not.toContainText("SEARCH_EMPTY");
  await expect(result).not.toContainText("CNY 980");
  await expect(result).not.toContainText("Publication id");
});
