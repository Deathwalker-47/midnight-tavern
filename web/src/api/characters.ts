import { apiFetch } from "./client";

export interface Character {
  id: string;
  user_id: string;
  name: string;
  description: string | null;
  personality: string | null;
  system_prompt: string | null;
  avatar_url: string | null;
  tags: string[];
  is_public: boolean;
  created_at: string;
  updated_at: string;
}

export interface CreateCharacterBody {
  name: string;
  description?: string;
  personality?: string;
  system_prompt?: string;
  avatar_url?: string;
  tags?: string[];
  is_public?: boolean;
}

export const charactersApi = {
  list: (includePublic = false) =>
    apiFetch<Character[]>(`/api/v1/characters?public=${includePublic}`),

  get: (id: string) => apiFetch<Character>(`/api/v1/characters/${id}`),

  create: (body: CreateCharacterBody) =>
    apiFetch<Character>("/api/v1/characters", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  update: (id: string, body: Partial<CreateCharacterBody>) =>
    apiFetch<Character>(`/api/v1/characters/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),

  delete: (id: string) =>
    apiFetch<void>(`/api/v1/characters/${id}`, { method: "DELETE" }),
};
