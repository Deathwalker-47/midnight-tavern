/**
 * Users API client — profile updates including the three-tier image
 * generation preference.
 */

import { apiFetch } from "./client";
import type { AuthUser } from "../store/authStore";

export type ImagePref = "auto" | "always" | "never";

export interface UpdateProfileRequest {
  display_name?: string | null;
  image_pref?: ImagePref;
}

export const usersApi = {
  updateProfile: (body: UpdateProfileRequest) =>
    apiFetch<AuthUser>("/api/v1/users/me", {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
};
