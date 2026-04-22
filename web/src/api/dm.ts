/**
 * Typed API client for the Dungeon Master module.
 * Mirrors the backend schemas in backend/app/modules/dungeon_master/schemas.py
 */

const BASE = "/api/v1/dm";

// ── Types ─────────────────────────────────────────────────────────────────────

export interface StatDefinition {
  name: string;
  display_name: string;
  type: "number" | "text" | "enum";
  initial_value?: number | string;
  min?: number;
  max?: number;
  options?: string[];
}

export interface Ruleset {
  id: string;
  name: string;
  description: string | null;
  stat_schema: StatDefinition[];
  rules_text: string | null;
  recommended_model: string | null;
  is_public: boolean;
  created_at: string;
  updated_at: string;
}

export interface DMSession {
  id: string;
  chat_id: string;
  ruleset_id: string;
  is_active: boolean;
  created_at: string;
}

export interface CharacterSheet {
  id: string;
  session_id: string;
  character_id: string | null;
  is_player: boolean;
  display_name: string | null;
  stats: Record<string, number | string>;
  inventory: string[];
  skills: Record<string, number | string>;
  updated_at: string;
}

export interface RollResult {
  id: string;
  expression: string;
  dice_faces: string;
  num_dice: number;
  modifier: number;
  all_rolls: number[];
  kept_rolls: number[];
  total: number;
  context: string | null;
  created_at: string;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

async function apiFetch<T>(
  url: string,
  options: RequestInit = {},
): Promise<T> {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json", ...options.headers },
    ...options,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: { message: res.statusText } }));
    throw new Error(err?.error?.message ?? res.statusText);
  }
  return res.json() as Promise<T>;
}

// ── Rulesets ──────────────────────────────────────────────────────────────────

export const dmApi = {
  createRuleset: (body: Omit<Ruleset, "id" | "created_at" | "updated_at">) =>
    apiFetch<Ruleset>(`${BASE}/rulesets`, { method: "POST", body: JSON.stringify(body) }),

  listRulesets: () => apiFetch<Ruleset[]>(`${BASE}/rulesets`),

  getRuleset: (id: string) => apiFetch<Ruleset>(`${BASE}/rulesets/${id}`),

  updateRuleset: (id: string, body: Partial<Omit<Ruleset, "id" | "created_at" | "updated_at">>) =>
    apiFetch<Ruleset>(`${BASE}/rulesets/${id}`, { method: "PUT", body: JSON.stringify(body) }),

  deleteRuleset: (id: string) =>
    apiFetch<void>(`${BASE}/rulesets/${id}`, { method: "DELETE" }),

  // ── Sessions ───────────────────────────────────────────────────────────────

  createSession: (chatId: string, rulesetId: string) =>
    apiFetch<DMSession>(`${BASE}/sessions`, {
      method: "POST",
      body: JSON.stringify({ chat_id: chatId, ruleset_id: rulesetId }),
    }),

  getSession: (chatId: string) => apiFetch<DMSession>(`${BASE}/sessions/${chatId}`),

  deleteSession: (sessionId: string) =>
    apiFetch<void>(`${BASE}/sessions/${sessionId}`, { method: "DELETE" }),

  // ── Character Sheets ───────────────────────────────────────────────────────

  listSheets: (sessionId: string) =>
    apiFetch<CharacterSheet[]>(`${BASE}/sessions/${sessionId}/sheet`),

  createSheet: (sessionId: string, body: Partial<CharacterSheet>) =>
    apiFetch<CharacterSheet>(`${BASE}/sessions/${sessionId}/sheet`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  updateSheet: (
    sessionId: string,
    sheetId: string,
    body: Partial<Pick<CharacterSheet, "stats" | "inventory" | "skills" | "display_name">>,
  ) =>
    apiFetch<CharacterSheet>(`${BASE}/sessions/${sessionId}/sheet/${sheetId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),

  // ── Dice ───────────────────────────────────────────────────────────────────

  roll: (sessionId: string, expression: string, context?: string) =>
    apiFetch<RollResult>(`${BASE}/sessions/${sessionId}/roll`, {
      method: "POST",
      body: JSON.stringify({ expression, context }),
    }),

  listRolls: (sessionId: string, limit = 20, offset = 0) =>
    apiFetch<{ rolls: RollResult[]; total: number }>(
      `${BASE}/sessions/${sessionId}/rolls?limit=${limit}&offset=${offset}`,
    ),

  // ── Evaluation (SSE) ───────────────────────────────────────────────────────

  /**
   * Returns a native EventSource connected to the DM evaluation stream.
   * Callers should add event listeners for each dm_* event type.
   */
  evaluateSSE: (
    sessionId: string,
    playerAction: string,
    recentMessages: Array<{ role: string; content: string }>,
    characterId?: string,
  ): EventSource => {
    // POST-based SSE via a pre-flight to get a stream token would be cleaner
    // for large payloads, but for now we encode params in a query string.
    // The router accepts a POST body — use fetch + ReadableStream instead of EventSource.
    // This helper is a placeholder; see useDMEvaluate hook for the full implementation.
    throw new Error("Use useDMEvaluate hook for SSE evaluation — EventSource doesn't support POST bodies.");
  },
};

/**
 * Trigger DM evaluation and process the SSE stream with a callback per event.
 * Returns a promise that resolves when the stream closes.
 */
export async function evaluateDMAction(
  sessionId: string,
  playerAction: string,
  recentMessages: Array<{ role: string; content: string }>,
  onEvent: (type: string, data: Record<string, unknown>) => void,
  characterId?: string,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${BASE}/sessions/${sessionId}/evaluate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      player_action: playerAction,
      recent_messages: recentMessages,
      character_id: characterId ?? null,
    }),
    signal,
  });

  if (!res.ok || !res.body) {
    throw new Error(`DM evaluate failed: ${res.statusText}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";

    let eventType = "";
    for (const line of lines) {
      if (line.startsWith("event: ")) {
        eventType = line.slice(7).trim();
      } else if (line.startsWith("data: ") && eventType) {
        try {
          const data = JSON.parse(line.slice(6)) as Record<string, unknown>;
          onEvent(eventType, data);
        } catch {
          // ignore malformed JSON
        }
        eventType = "";
      }
    }
  }
}
