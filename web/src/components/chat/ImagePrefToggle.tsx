/**
 * Cycles through image generation preferences: auto → always → never → auto.
 *
 * Persists the choice via PATCH /users/me. Kept in its own file so it can be
 * exercised by Vitest without dragging in the full ChatPage tree.
 */

import { usersApi, type ImagePref } from "../../api/users";
import { useAuthStore } from "../../store/authStore";

const CYCLE: Record<ImagePref, ImagePref> = {
  auto: "always",
  always: "never",
  never: "auto",
};

const LABEL: Record<ImagePref, string> = {
  auto: "🪄 Images: Auto",
  always: "🖼️ Images: Always",
  never: "🚫 Images: Never",
};

export function ImagePrefToggle() {
  const user = useAuthStore((s) => s.user);
  const setUser = useAuthStore((s) => s.setUser);
  const pref: ImagePref = (user?.image_pref ?? "auto") as ImagePref;

  async function onClick() {
    const next = CYCLE[pref];
    try {
      const updated = await usersApi.updateProfile({ image_pref: next });
      setUser(updated);
    } catch {
      // silently fail — user can retry
    }
  }

  return (
    <button
      type="button"
      onClick={onClick}
      title="Cycle through Auto / Always / Never image generation"
      className="text-xs text-gray-500 hover:text-gray-300 transition-colors"
    >
      {LABEL[pref]}
    </button>
  );
}
