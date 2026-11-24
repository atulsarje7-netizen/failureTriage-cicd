import { expect, flow, test } from "../fixtures";
import { CartPage } from "../pages/cart.page";
import { CatalogPage } from "../pages/catalog.page";
import { CheckoutPage } from "../pages/checkout.page";
import { LoginPage } from "../pages/login.page";

test("checkout places an order visible in history", flow("checkout"), async ({ page }) => {
  const login = new LoginPage(page);
  await login.goto();
  await login.login("alice", "password");
  const catalog = new CatalogPage(page);
  await catalog.goto();
  await catalog.addFirstProduct();
  const cart = new CartPage(page);
  await cart.goto();
  await cart.goToCheckout();
  const checkout = new CheckoutPage(page);
  await checkout.placeOrder();
  await expect(page.getByTestId("order-placed")).toBeVisible();
  await expect(page.getByTestId("order-row").first()).toBeVisible();
});
