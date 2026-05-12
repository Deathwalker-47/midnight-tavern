import { apiFetch } from "./client";
import type { AuthUser } from "../store/authStore";

export const authApi = {
  register: (username: string, email: string, password: string, display_name?: string) =>
    apiFetch<AuthUser>("/api/v1/auth/register", {
      method: "POST",
      body: JSON.stringify({ username, email, password, display_name }),
    }),

  login: (username: string, password: string) =>
    apiFetch<AuthUser>("/api/v1/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),

  logout: () => apiFetch<void>("/api/v1/auth/logout", { method: "POST" }),

  me: () => apiFetch<AuthUser>("/api/v1/auth/me"),
};
