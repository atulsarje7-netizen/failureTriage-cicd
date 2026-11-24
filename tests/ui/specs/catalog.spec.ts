import { expect, flow, test } from "../fixtures";
import { CartPage } from "../pages/cart.page";
import { CatalogPage } from "../pages/catalog.page";
import { LoginPage } from "../pages/login.page";

test("catalog lists seeded products", flow("catalog"), async ({ page }) => {
  const catalog = new CatalogPage(page);
  await catalog.goto();
  await expect(page.getByTestId("product-card")).toHaveCount(3);
});

test("add to cart updates the badge", flow("catalog"), async ({ page }) => {
  const login = new LoginPage(page);
  await login.goto();
  await login.login("alice", "password");
  await new CartPage(page).empty();
  const catalog = new CatalogPage(page);
  await catalog.goto();
  await catalog.addFirstProduct();
  await expect(page.getByTestId("cart-count")).toHaveText("1");
});
