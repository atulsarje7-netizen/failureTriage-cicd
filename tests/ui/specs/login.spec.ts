import { expect, flow, test } from "../fixtures";
import { LoginPage } from "../pages/login.page";

test("guest can open login", flow("login"), async ({ page }) => {
  const login = new LoginPage(page);
  await login.goto();
  await expect(page.getByTestId("login-form")).toBeVisible();
});

test("wrong password shows an error", flow("login"), async ({ page }) => {
  const login = new LoginPage(page);
  await login.goto();
  await login.login("alice", "wrong");
  await expect(page.getByTestId("login-error")).toBeVisible();
});

test("alice can log in and reach the catalog", flow("login"), async ({ page }) => {
  const login = new LoginPage(page);
  await login.goto();
  await login.login("alice", "password");
  await expect(page.getByTestId("catalog-heading")).toBeVisible();
  await expect(page.getByTestId("nav-user")).toHaveText("alice");
});
