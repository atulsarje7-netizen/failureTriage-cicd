import { test as base, expect } from "@playwright/test";

/** Playwright details object that tags a test with a shop flow id. */
export function flow(name: string) {
  return { annotation: [{ type: "flow" as const, description: name }] };
}

export const test = base.extend({
  page: async ({ page }, use, testInfo) => {
    await page.setExtraHTTPHeaders({
      "X-Test-Id": testInfo.titlePath.join(" > "),
    });
    await use(page);
  },
});

export { expect };
