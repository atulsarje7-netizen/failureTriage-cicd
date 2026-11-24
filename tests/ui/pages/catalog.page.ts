import type { Page } from "@playwright/test";

/** Product catalog at /catalog. */
export class CatalogPage {
  constructor(private readonly page: Page) {}

  /** Open the catalog. */
  async goto() {
    await this.page.goto("/catalog");
  }

  /** Add the first listed product to the cart via HTMX. */
  async addFirstProduct() {
    await this.page.getByTestId("add-to-cart").first().click();
  }
}
