import type { Page } from "@playwright/test";

/** Login form at /login. */
export class LoginPage {
  constructor(private readonly page: Page) {}

  /** Open the login page. */
  async goto() {
    await this.page.goto("/login");
  }

  /** Submit username and password. */
  async login(username: string, password: string) {
    await this.page.getByTestId("username").fill(username);
    await this.page.getByTestId("password").fill(password);
    await this.page.getByTestId("login-submit").click();
  }
}
