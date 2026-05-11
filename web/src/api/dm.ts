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
  description?: string;
  is_resource_bar?: boolean;
  bar_color?: string;
}

export interface EnforcementConfig {
  require_skill_for_ability: boolean;
  require_item_for_use: boolean;
  permadeath: boolean;
  no_invented_powers: boolean;
  resource_costs_enforced: boolean;
  dead_cannot_act: boolean;
  respect_conditions: boolean;
}

export interface Ruleset {
  id: string;
  name: string;
  description: string | null;
  stat_schema: StatDefinition[];
  rules_text: string | null;
  reminder_text: string | null;
  instruction: string | null;
  player_guide: string | null;
  recommended_model: string | null;
  recommended_provider: string | null;
  dm_temperature: number;
  dm_max_tokens: number;
  context_window: number;
  enforcement_config: EnforcementConfig | null;
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

export interface SpellEntry {
  name: string;
  circle?: number;
  mp_cost?: number;
  prepared?: boolean;
  element?: string;
  source?: string;
}

export interface CharacterSheet {
  id: string;
  session_id: string;
  character_id: string | null;
  is_player: boolean;
  display_name: string | null;
  description: string | null;
  stats: Record<string, number | string>;
  inventory: string[];
  skills: Record<string, number | string>;
  spells: SpellEntry[];
  is_alive: boolean;
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
  dc: number | null;
  success: boolean | null;
  advantage: boolean;
  disadvantage: boolean;
  stat_used: string | null;
  created_at: string;
}

export interface RollStatsResponse {
  total_rolls: number;
  nat20_count: number;
  nat1_count: number;
  avg_roll: number | null;
  highest: number | null;
  lowest: number | null;
}

export interface GameEventStatChange {
  name: string;
  display_name: string;
  old_value: number | string;
  new_value: number | string;
  delta: number | null;
}

export interface GameEventResponse {
  id: string;
  session_id: string;
  message_id: string | null;
  player_action: string;
  ruling: "pass" | "roll" | "reject" | "partial" | "ask";
  ruling_reason: string | null;
  approved_action: string | null;
  forbidden_elements: string[];
  stat_changes: GameEventStatChange[];
  inventory_changes: string[];
  skill_changes: Record<string, unknown>[];
  dm_model_used: string | null;
  dm_prompt_tokens: number | null;
  dm_completion_tokens: number | null;
  dm_latency_ms: number | null;
  pre_validation: Record<string, unknown> | null;
  question_text: string | null;
  player_answer: string | null;
  summary: string | null;
  created_at: string;
}

export interface GameEventListResponse {
  events: GameEventResponse[];
  total: number;
  has_more: boolean;
}

export interface AttachmentResponse {
  id: string;
  ruleset_id: string;
  character_id: string | null;
  chat_id: string | null;
  mode: "required" | "optional";
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
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

// ── Rulesets ──────────────────────────────────────────────────────────────────

export const dmApi = {
  createRuleset: (body: Omit<Ruleset, "id" | "created_at" | "updated_at">) =>
    apiFetch<Ruleset>(`${BASE}/rulesets`, { method: "POST", body: JSON.stringify(body) }),

  listRulesets: () => apiFetch<Ruleset[]>(`${BASE}/rulesets`),

  listPublicRulesets: () => apiFetch<Ruleset[]>(`${BASE}/rulesets/public`),

  getRuleset: (id: string) => apiFetch<Ruleset>(`${BASE}/rulesets/${id}`),

  updateRuleset: (id: string, body: Partial<Omit<Ruleset, "id" | "created_at" | "updated_at">>) =>
    apiFetch<Ruleset>(`${BASE}/rulesets/${id}`, { method: "PUT", body: JSON.stringify(body) }),

  deleteRuleset: (id: string) =>
    apiFetch<void>(`${BASE}/rulesets/${id}`, { method: "DELETE" }),

  duplicateRuleset: (id: string) =>
    apiFetch<Ruleset>(`${BASE}/rulesets/${id}/duplicate`, { method: "POST" }),

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
    body: Partial<Pick<CharacterSheet, "stats" | "inventory" | "skills" | "spells" | "display_name" | "description" | "is_alive">>,
  ) =>
    apiFetch<CharacterSheet>(`${BASE}/sessions/${sessionId}/sheet/${sheetId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),

  // ── Dice ───────────────────────────────────────────────────────────────────

  roll: (
    sessionId: string,
    expression: string,
    context?: string,
    dc?: number,
    advantage?: boolean,
    disadvantage?: boolean,
    stat_used?: string,
  ) =>
    apiFetch<RollResult>(`${BASE}/sessions/${sessionId}/roll`, {
      method: "POST",
      body: JSON.stringify({ expression, context, dc, advantage, disadvantage, stat_used }),
    }),

  listRolls: (sessionId: string, limit = 20, offset = 0) =>
    apiFetch<{ rolls: RollResult[]; total: number }>(
      `${BASE}/sessions/${sessionId}/rolls?limit=${limit}&offset=${offset}`,
    ),

  getRollStats: (sessionId: string) =>
    apiFetch<RollStatsResponse>(`${BASE}/sessions/${sessionId}/rolls/stats`),

  // ── Game Events ────────────────────────────────────────────────────────────

  listEvents: (sessionId: string, page = 1, pageSize = 20) =>
    apiFetch<GameEventListResponse>(
      `${BASE}/sessions/${sessionId}/events?page=${page}&page_size=${pageSize}`,
    ),

  getEvent: (sessionId: string, eventId: string) =>
    apiFetch<GameEventResponse>(`${BASE}/sessions/${sessionId}/events/${eventId}`),

  answerQuestion: (sessionId: string, eventId: string, answer: string) =>
    apiFetch<GameEventResponse>(`${BASE}/sessions/${sessionId}/answer`, {
      method: "POST",
      body: JSON.stringify({ event_id: eventId, answer }),
    }),

  // ── Attachments ────────────────────────────────────────────────────────────

  createAttachment: (body: {
    ruleset_id: string;
    character_id?: string;
    chat_id?: string;
    mode?: "required" | "optional";
  }) =>
    apiFetch<AttachmentResponse>(`${BASE}/attachments`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  listAttachments: () => apiFetch<AttachmentResponse[]>(`${BASE}/attachments`),

  deleteAttachment: (id: string) =>
    apiFetch<void>(`${BASE}/attachments/${id}`, { method: "DELETE" }),

  // ── Evaluation (SSE) ───────────────────────────────────────────────────────

  /**
   * Returns a native EventSource connected to the DM evaluation stream.
   * Callers should add event listeners for each dm_* event type.
   */
  evaluateSSE: (
    _sessionId: string,
    _playerAction: string,
    _recentMessages: Array<{ role: string; content: string }>,
    _characterId?: string,
  ): EventSource => {
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
