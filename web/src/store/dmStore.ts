/**
 * Zustand store for Dungeon Master session state.
 * Updated in real-time from the DM SSE event stream.
 */

import { create } from "zustand";
import { dmApi, type SpellEntry } from "../api/dm";

export interface StatChange {
  name: string;
  displayName: string;
  oldValue: number | string;
  newValue: number | string;
  delta: number | null;
}

export interface DMRoll {
  rollId: string;
  expression: string;
  allRolls: number[];
  keptRolls: number[];
  modifier: number;
  total: number;
  context?: string;
  timestamp: number;
}

export interface DMEvalResult {
  verdict: "continue" | "reject" | "ask";
  summary?: string;
  narrativeHint?: string;
  rejectionReason?: string;
  askQuestion?: string;
  statChanges: StatChange[];
  rollIds: string[];
  deathState: boolean;
  gameEventId: string | null;
}

export interface DMSessionState {
  sessionId: string | null;
  rulesetId: string | null;
  isActive: boolean;

  // Character sheet
  stats: Record<string, number | string>;
  inventory: string[];
  skills: Record<string, number | string>;
  spells: SpellEntry[];
  description: string | null;
  isAlive: boolean;

  // Roll history (most recent first, capped at 50)
  rolls: DMRoll[];

  // Current evaluation state
  isEvaluating: boolean;
  pendingQuestion: string | null;
  pendingEventId: string | null;
  lastRejection: string | null;
  lastResult: DMEvalResult | null;
  deathState: boolean;
}

interface DMActions {
  setSession: (sessionId: string, rulesetId: string) => void;
  clearSession: () => void;
  updateStats: (newStats: Record<string, number | string>) => void;
  updateInventory: (items: string[]) => void;
  updateSkills: (skills: Record<string, number | string>) => void;
  updateSpells: (spells: SpellEntry[]) => void;
  setDescription: (description: string | null) => void;
  setIsAlive: (value: boolean) => void;
  addRoll: (roll: DMRoll) => void;
  setEvaluating: (value: boolean) => void;
  setPendingQuestion: (question: string | null) => void;
  setPendingEventId: (id: string | null) => void;
  setLastRejection: (reason: string | null) => void;
  setLastResult: (result: DMEvalResult) => void;
  setDeathState: (value: boolean) => void;
  clearEvalState: () => void;
  answerQuestion: (sessionId: string, eventId: string, answer: string) => Promise<void>;
}

const initialState: DMSessionState = {
  sessionId: null,
  rulesetId: null,
  isActive: false,
  stats: {},
  inventory: [],
  skills: {},
  spells: [],
  description: null,
  isAlive: true,
  rolls: [],
  isEvaluating: false,
  pendingQuestion: null,
  pendingEventId: null,
  lastRejection: null,
  lastResult: null,
  deathState: false,
};

export const useDMStore = create<DMSessionState & DMActions>((set) => ({
  ...initialState,

  setSession: (sessionId, rulesetId) =>
    set({ sessionId, rulesetId, isActive: true }),

  clearSession: () => set(initialState),

  updateStats: (newStats) => set({ stats: newStats }),

  updateInventory: (items) => set({ inventory: items }),

  updateSkills: (skills) => set({ skills }),

  updateSpells: (spells) => set({ spells }),

  setDescription: (description) => set({ description }),

  setIsAlive: (value) => set({ isAlive: value }),

  addRoll: (roll) =>
    set((state) => ({
      rolls: [roll, ...state.rolls].slice(0, 50),
    })),

  setEvaluating: (value) => set({ isEvaluating: value }),

  setPendingQuestion: (question) => set({ pendingQuestion: question }),

  setPendingEventId: (id) => set({ pendingEventId: id }),

  setLastRejection: (reason) => set({ lastRejection: reason }),

  setLastResult: (result) =>
    set({
      lastResult: result,
      deathState: result.deathState || false,
    }),

  setDeathState: (value) => set({ deathState: value }),

  clearEvalState: () =>
    set({
      isEvaluating: false,
      pendingQuestion: null,
      lastRejection: null,
    }),

  answerQuestion: async (sessionId, eventId, answer) => {
    await dmApi.answerQuestion(sessionId, eventId, answer);
    set({ pendingQuestion: null, pendingEventId: null });
  },
}));

// ── SSE event handler ─────────────────────────────────────────────────────────

/**
 * Process a DM SSE event and update the store accordingly.
 * Call this from the chat's SSE event listener.
 */
export function handleDMEvent(
  eventType: string,
  data: Record<string, unknown>,
): void {
  const store = useDMStore.getState();

  switch (eventType) {
    case "dm_thinking":
      store.setEvaluating(true);
      store.clearEvalState();
      break;

    case "dm_roll":
      store.addRoll({
        rollId: data.roll_id as string,
        expression: data.expression as string,
        allRolls: data.all_rolls as number[],
        keptRolls: data.kept_rolls as number[],
        modifier: (data.modifier as number) ?? 0,
        total: data.total as number,
        context: data.context as string | undefined,
        timestamp: Date.now(),
      });
      break;

    case "dm_stat_update":
      if (data.new_stats) {
        store.updateStats(data.new_stats as Record<string, number | string>);
      }
      if (data.death_state) {
        store.setDeathState(true);
        store.setIsAlive(false);
      }
      break;

    case "dm_inventory_update":
      if (data.inventory) {
        store.updateInventory(data.inventory as string[]);
      }
      break;

    case "dm_skill_update":
      if (data.skills) {
        store.updateSkills(data.skills as Record<string, number | string>);
      }
      break;

    case "dm_spell_update":
      if (data.spells) {
        store.updateSpells(data.spells as SpellEntry[]);
      }
      break;

    case "dm_description_update":
      if (data.description !== undefined) {
        store.setDescription(data.description as string | null);
      }
      break;

    case "dm_reject":
      store.setLastRejection(data.reason as string);
      store.setEvaluating(false);
      break;

    case "dm_ask":
      store.setPendingQuestion(data.question as string);
      store.setEvaluating(false);
      break;

    case "dm_done": {
      // Map snake_case backend fields → camelCase frontend types
      const rawChanges = (
        data.stat_changes as Array<{
          name: string;
          display_name: string;
          old_value: number | string;
          new_value: number | string;
          delta: number | null;
        }>
      ) ?? [];
      const statChanges: StatChange[] = rawChanges.map((c) => ({
        name: c.name,
        displayName: c.display_name,
        oldValue: c.old_value,
        newValue: c.new_value,
        delta: c.delta,
      }));
      const result: DMEvalResult = {
        verdict: data.verdict as "continue" | "reject" | "ask",
        summary: data.summary as string | undefined,
        rejectionReason: data.rejection_reason as string | undefined,
        statChanges,
        rollIds: (data.roll_ids as string[]) ?? [],
        deathState: (data.death_state as boolean) ?? false,
        gameEventId: (data.game_event_id as string) ?? null,
      };
      store.setLastResult(result);
      if (result.verdict === "ask" && result.gameEventId) {
        store.setPendingEventId(result.gameEventId);
      }
      store.setEvaluating(false);
      break;
    }
  }
}
