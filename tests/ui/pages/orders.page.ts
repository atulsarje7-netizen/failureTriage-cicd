import type { Page } from "@playwright/test";

/** Order history at /history. */
export class OrdersPage {
  constructor(private readonly page: Page) {}

  /** Open order history. */
  async goto() {
    await this.page.goto("/history");
  }
}
