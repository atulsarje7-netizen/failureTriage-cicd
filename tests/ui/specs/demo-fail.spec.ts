import { expect, flow, test } from "../fixtures";

test("deliberate failure for trace artifacts", flow("login"), async ({ page }) => {
  test.skip(process.env.DEMO_FAIL !== "1", "opt-in artifact demo");
  await page.goto("/login");
  await expect(page.getByTestId("login-form")).toHaveCount(0);
});
