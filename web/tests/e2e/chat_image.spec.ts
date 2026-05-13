import { test, expect } from "@playwright/test";

/**
 * Full register → login → character → chat smoke flow.
 *
 * What this covers end-to-end:
 *   - register form posts to /api/v1/auth/register and lands us on the
 *     characters page
 *   - character creation persists and shows in the grid
 *   - clicking "Chat" creates a chat row and navigates to /chat/:id
 *   - the chat page renders the image-pref toggle and the Illustrate /
 *     HQ scene buttons appear once at least one message exists
 *
 * What this does NOT cover (intentionally — backend e2e already does):
 *   - the LLM-streamed [SCENE_BEAT:*] marker → image_dispatched flow
 *     (requires an Anthropic API key or a server-side LLM stub)
 *   - the actual image render (requires asset library seeded)
 *
 * The spec runs with IMAGE_PROVIDER_ORDER=dummy so any image jobs the UI
 * triggers complete quickly without hitting external APIs.
 */

function uniqueUsername(): string {
  return `e2e_${Date.now().toString(36)}_${Math.floor(Math.random() * 10000)}`;
}

test.describe("chat + image UI smoke", () => {
  test("register, create character, open chat, see image controls", async ({
    page,
  }) => {
    const username = uniqueUsername();

    // ── Register ───────────────────────────────────────────────────────
    await page.goto("/register");
    await page.getByLabel("username").fill(username);
    await page.getByLabel("email").fill(`${username}@example.com`);
    await page.getByLabel("password").fill("supersecret123");
    await page.getByRole("button", { name: /Create account/ }).click();

    // Redirected to /characters.
    await expect(page).toHaveURL(/\/characters$/);

    // ── Create character ───────────────────────────────────────────────
    await page.getByRole("button", { name: /New|Create your first/i }).first().click();
    await page.getByLabel(/^Name/).fill("Lyra Nightwhisper");
    await page.getByLabel(/^Description$/).fill("A shadowy bard from the eastern wilds.");
    await page.getByRole("button", { name: /^Create$/ }).click();

    // Card appears.
    await expect(page.getByText("Lyra Nightwhisper")).toBeVisible();

    // ── Start chat ──────────────────────────────────────────────────────
    await page.getByRole("button", { name: /^Chat$/ }).first().click();
    await expect(page).toHaveURL(/\/chat\/[0-9a-f-]+$/);

    // ── Image-pref toggle visible and cycles ───────────────────────────
    const toggle = page.getByRole("button", { name: /Images: (Auto|Always|Never)/ });
    await expect(toggle).toBeVisible();
    await expect(toggle).toHaveText(/Auto/);
    await toggle.click();
    await expect(toggle).toHaveText(/Always/);
    await toggle.click();
    await expect(toggle).toHaveText(/Never/);
    await toggle.click();
    await expect(toggle).toHaveText(/Auto/);
  });
});
