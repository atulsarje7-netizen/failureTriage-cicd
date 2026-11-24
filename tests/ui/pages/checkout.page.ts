import type { Page } from "@playwright/test";

/** Checkout confirmation at /checkout. */
export class CheckoutPage {
  constructor(private readonly page: Page) {}

  /** Submit the place-order form. */
  async placeOrder() {
    await this.page.getByTestId("place-order").click();
  }
}
