import type { Page } from "@playwright/test";

/** Cart at /basket. */
export class CartPage {
  constructor(private readonly page: Page) {}

  /** Open the cart. */
  async goto() {
    await this.page.goto("/basket");
  }

  /** Remove the first cart line via HTMX. */
  async removeFirst() {
    await this.page.getByTestId("remove-from-cart").first().click();
  }

  /** Remove every line until the cart is empty. */
  async empty() {
    await this.goto();
    const empty = this.page.getByTestId("cart-empty");
    for (let i = 0; i < 8; i++) {
      if (await empty.isVisible()) {
        return;
      }
      const remove = this.page.getByTestId("remove-from-cart").first();
      if (!(await remove.isVisible())) {
        return;
      }
      await remove.click();
    }
  }

  /** Go to the checkout confirmation page. */
  async goToCheckout() {
    await this.page.getByTestId("go-checkout").click();
  }
}
