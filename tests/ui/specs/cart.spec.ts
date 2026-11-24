import { expect, flow, test } from "../fixtures";
import { CartPage } from "../pages/cart.page";
import { CatalogPage } from "../pages/catalog.page";
import { LoginPage } from "../pages/login.page";

test("anonymous cart visit redirects to login", flow("cart"), async ({ page }) => {
  await page.goto("/basket");
  await expect(page.getByTestId("login-form")).toBeVisible();
});

test("remove item leaves an empty cart", flow("cart"), async ({ page }) => {
  const login = new LoginPage(page);
  await login.goto();
  await login.login("alice", "password");
  const cart = new CartPage(page);
  await cart.empty();
  const catalog = new CatalogPage(page);
  await catalog.goto();
  await catalog.addFirstProduct();
  await cart.goto();
  await expect(page.getByTestId("cart-line")).toHaveCount(1);
  await cart.removeFirst();
  await expect(page.getByTestId("cart-empty")).toBeVisible();
});
