/**
 * DM Config Editor — create or edit a game ruleset with full stat schema builder.
 */

import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { dmApi, type Ruleset, type StatDefinition } from "../api/dm";
import { ApiError } from "../api/client";

// ── Classic d20 RPG template ──────────────────────────────────────────────────

const D20_TEMPLATE = {
  name: "Classic d20 RPG",
  description: "Standard fantasy RPG with d20 resolution, 6 core stats, HP/MP tracking, class restrictions, and permadeath.",
  rules_text: `Combat uses d20 + relevant stat modifier vs target's defense or a DC.
- Attack roll: d20 + STR (melee) or DEX (ranged) vs target AC
- Skill checks: d20 + relevant stat modifier vs DC set by situation (Easy=10, Medium=15, Hard=20, Deadly=25)
- Saving throws: d20 + CON/DEX/WIS vs spell or effect DC
- Spellcasting: Only Mage and Healer classes can cast spells. Costs MP per spell level.
- Death: HP reaching 0 triggers permadeath. Character cannot be revived.
- Class restrictions: Warriors cannot cast spells. Rogues get +2 to DEX checks. Healers can revive others before death.`,
  reminder_text: `CRITICAL: Dead characters (HP=0 or is_alive=false) CANNOT act. Hard reject immediately.
Only Mage and Healer can cast spells — reject spell use from other classes.
Always roll dice for uncertain outcomes. Never assume success without a roll.`,
  instruction: "This session uses the Classic d20 RPG ruleset. All mechanical outcomes are determined by the Dungeon Master before narration.",
  player_guide: "## Classic d20 RPG\nAll outcomes decided by d20 dice + your stats.\n\n**Death is permanent.** No resurrection.\n\n**Class matters.** Only Healers and Mages cast magic.\n\nJust play naturally — the DM handles all mechanics automatically.",
  recommended_model: "claude-haiku-4-5-20251001",
  recommended_provider: "anthropic",
  dm_temperature: 0.3,
  dm_max_tokens: 2000,
  context_window: 10,
  enforcement_config: {
    require_skill_for_ability: true,
    require_item_for_use: true,
    permadeath: true,
    no_invented_powers: true,
    resource_costs_enforced: true,
    dead_cannot_act: true,
    respect_conditions: true,
  },
  stat_schema: [
    { name: "HP", display_name: "Hit Points", type: "number" as const, initial_value: 50, min: 0, max: 100, description: "Hit points. HP ≤ 0 = dead.", is_resource_bar: true, bar_color: "#ef4444" },
    { name: "MP", display_name: "Mana Points", type: "number" as const, initial_value: 20, min: 0, max: 50, description: "Mana points. Spells cost MP.", is_resource_bar: true, bar_color: "#3b82f6" },
    { name: "Class", display_name: "Class", type: "enum" as const, initial_value: "Warrior", options: ["Warrior", "Mage", "Rogue", "Ranger", "Healer", "Bard"], description: "Character class. Only Healer and Mage can cast spells.", is_resource_bar: false },
    { name: "Condition", display_name: "Condition", type: "text" as const, initial_value: "", description: "Active conditions e.g. 'Poisoned (3t)'. Clear at 0.", is_resource_bar: false },
    { name: "STR", display_name: "Strength", type: "number" as const, initial_value: 10, min: 1, max: 20, description: "Melee attacks, physical force.", is_resource_bar: false },
    { name: "DEX", display_name: "Dexterity", type: "number" as const, initial_value: 10, min: 1, max: 20, description: "Ranged attacks, dodge, stealth.", is_resource_bar: false },
    { name: "CON", display_name: "Constitution", type: "number" as const, initial_value: 10, min: 1, max: 20, description: "Health, endurance, poison resistance.", is_resource_bar: false },
    { name: "INT", display_name: "Intelligence", type: "number" as const, initial_value: 10, min: 1, max: 20, description: "Magic power, knowledge, investigation.", is_resource_bar: false },
    { name: "WIS", display_name: "Wisdom", type: "number" as const, initial_value: 10, min: 1, max: 20, description: "Perception, willpower, healing.", is_resource_bar: false },
    { name: "CHA", display_name: "Charisma", type: "number" as const, initial_value: 10, min: 1, max: 20, description: "Persuasion, deception, intimidation.", is_resource_bar: false },
    { name: "Level", display_name: "Level", type: "number" as const, initial_value: 1, min: 1, max: 20, description: "Character level.", is_resource_bar: false },
    { name: "XP", display_name: "Experience", type: "number" as const, initial_value: 0, min: 0, description: "Experience points. Lv2=100, Lv3=300, Lv4=600, Lv5=1000.", is_resource_bar: false },
    { name: "Gold", display_name: "Gold", type: "number" as const, initial_value: 50, min: 0, description: "Currency.", is_resource_bar: false },
  ] as StatDefinition[],
  is_public: false,
};

// ── Types ─────────────────────────────────────────────────────────────────────

interface EnforcementConfig {
  require_skill_for_ability: boolean;
  require_item_for_use: boolean;
  permadeath: boolean;
  no_invented_powers: boolean;
  resource_costs_enforced: boolean;
  dead_cannot_act: boolean;
  respect_conditions: boolean;
}

interface FormState {
  name: string;
  description: string;
  rules_text: string;
  reminder_text: string;
  instruction: string;
  player_guide: string;
  recommended_model: string;
  recommended_provider: string;
  dm_temperature: number;
  dm_max_tokens: number;
  context_window: number;
  enforcement_config: EnforcementConfig;
  stat_schema: StatDefinition[];
  is_public: boolean;
}

const BLANK_STAT: StatDefinition = {
  name: "",
  display_name: "",
  type: "number",
  initial_value: 0,
  min: undefined,
  max: undefined,
  options: [],
  description: "",
  is_resource_bar: false,
  bar_color: "",
};

const DEFAULT_ENFORCEMENT: EnforcementConfig = {
  require_skill_for_ability: true,
  require_item_for_use: true,
  permadeath: true,
  no_invented_powers: true,
  resource_costs_enforced: true,
  dead_cannot_act: true,
  respect_conditions: true,
};

const ENFORCEMENT_LABELS: Record<keyof EnforcementConfig, string> = {
  require_skill_for_ability: "Require skill for ability use",
  require_item_for_use: "Require item in inventory before use",
  permadeath: "Permadeath (HP=0 is permanent death)",
  no_invented_powers: "No invented abilities not in skill list",
  resource_costs_enforced: "Enforce resource costs (MP/Stamina)",
  dead_cannot_act: "Dead characters cannot act",
  respect_conditions: "Enforce status conditions (Paralyzed, Stunned, etc.)",
};

// ── Stat row editor ───────────────────────────────────────────────────────────

function StatRow({
  stat,
  index,
  total,
  onChange,
  onRemove,
  onMove,
}: {
  stat: StatDefinition;
  index: number;
  total: number;
  onChange: (s: StatDefinition) => void;
  onRemove: () => void;
  onMove: (dir: -1 | 1) => void;
}) {
  const set = <K extends keyof StatDefinition>(k: K) =>
    (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) => {
      const val = e.target.type === "checkbox"
        ? (e.target as HTMLInputElement).checked
        : e.target.value;
      onChange({ ...stat, [k]: val });
    };

  return (
    <div className="border border-gray-700 rounded-lg p-3 space-y-2 bg-gray-850">
      <div className="flex items-center gap-2">
        <div className="flex flex-col gap-0.5">
          <button type="button" disabled={index === 0} onClick={() => onMove(-1)} className="text-gray-500 hover:text-gray-300 disabled:opacity-20 text-xs leading-none">▲</button>
          <button type="button" disabled={index === total - 1} onClick={() => onMove(1)} className="text-gray-500 hover:text-gray-300 disabled:opacity-20 text-xs leading-none">▼</button>
        </div>
        <input value={stat.name} onChange={set("name")} placeholder="name (snake_case)" className="flex-1 px-2 py-1 rounded bg-gray-800 border border-gray-700 text-gray-100 text-xs font-mono focus:outline-none focus:border-indigo-500" />
        <input value={stat.display_name} onChange={set("display_name")} placeholder="Display Name" className="flex-1 px-2 py-1 rounded bg-gray-800 border border-gray-700 text-gray-100 text-xs focus:outline-none focus:border-indigo-500" />
        <select value={stat.type} onChange={set("type")} className="px-2 py-1 rounded bg-gray-800 border border-gray-700 text-gray-100 text-xs focus:outline-none">
          <option value="number">number</option>
          <option value="text">text</option>
          <option value="enum">enum</option>
        </select>
        <button type="button" onClick={onRemove} className="text-red-500 hover:text-red-400 text-sm ml-auto">×</button>
      </div>

      <div className="flex flex-wrap gap-2 items-center text-xs">
        {stat.type === "number" && (
          <>
            <label className="flex items-center gap-1 text-gray-400">
              min <input type="number" value={stat.min ?? ""} onChange={(e) => onChange({ ...stat, min: e.target.value === "" ? undefined : Number(e.target.value) })} className="w-16 px-1 py-0.5 rounded bg-gray-800 border border-gray-700 text-gray-100 focus:outline-none" />
            </label>
            <label className="flex items-center gap-1 text-gray-400">
              max <input type="number" value={stat.max ?? ""} onChange={(e) => onChange({ ...stat, max: e.target.value === "" ? undefined : Number(e.target.value) })} className="w-16 px-1 py-0.5 rounded bg-gray-800 border border-gray-700 text-gray-100 focus:outline-none" />
            </label>
            <label className="flex items-center gap-1 text-gray-400">
              initial <input type="number" value={stat.initial_value as number} onChange={(e) => onChange({ ...stat, initial_value: Number(e.target.value) })} className="w-16 px-1 py-0.5 rounded bg-gray-800 border border-gray-700 text-gray-100 focus:outline-none" />
            </label>
            <label className="flex items-center gap-1 text-gray-400">
              <input type="checkbox" checked={stat.is_resource_bar ?? false} onChange={(e) => onChange({ ...stat, is_resource_bar: e.target.checked })} />
              resource bar
            </label>
            {stat.is_resource_bar && (
              <label className="flex items-center gap-1 text-gray-400">
                color <input type="color" value={stat.bar_color || "#6366f1"} onChange={(e) => onChange({ ...stat, bar_color: e.target.value })} className="w-8 h-6 rounded cursor-pointer border-0 bg-transparent" />
              </label>
            )}
          </>
        )}
        {stat.type === "text" && (
          <label className="flex items-center gap-1 text-gray-400 w-full">
            default <input value={stat.initial_value as string} onChange={(e) => onChange({ ...stat, initial_value: e.target.value })} className="flex-1 px-2 py-0.5 rounded bg-gray-800 border border-gray-700 text-gray-100 focus:outline-none" />
          </label>
        )}
        {stat.type === "enum" && (
          <label className="flex items-center gap-1 text-gray-400 w-full">
            options (comma-separated)
            <input value={(stat.options ?? []).join(",")} onChange={(e) => onChange({ ...stat, options: e.target.value.split(",").map(s => s.trim()).filter(Boolean) })} className="flex-1 px-2 py-0.5 rounded bg-gray-800 border border-gray-700 text-gray-100 focus:outline-none" />
          </label>
        )}
      </div>
      <input value={stat.description ?? ""} onChange={set("description")} placeholder="Description for DM (optional)" className="w-full px-2 py-1 rounded bg-gray-800 border border-gray-700 text-gray-400 text-xs focus:outline-none focus:border-indigo-500" />
    </div>
  );
}

// ── Main editor ───────────────────────────────────────────────────────────────

export function DMConfigEditorPage() {
  const { configId } = useParams<{ configId?: string }>();
  const navigate = useNavigate();
  const isEdit = Boolean(configId);

  const [form, setForm] = useState<FormState>({
    name: "",
    description: "",
    rules_text: "",
    reminder_text: "",
    instruction: "",
    player_guide: "",
    recommended_model: "claude-haiku-4-5-20251001",
    recommended_provider: "anthropic",
    dm_temperature: 0.3,
    dm_max_tokens: 2000,
    context_window: 10,
    enforcement_config: { ...DEFAULT_ENFORCEMENT },
    stat_schema: [],
    is_public: false,
  });
  const [loading, setLoading] = useState(isEdit);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<"basic" | "rules" | "stats" | "model" | "enforcement">("basic");

  useEffect(() => {
    if (!configId) return;
    dmApi.getRuleset(configId).then((r: Ruleset) => {
      setForm({
        name: r.name,
        description: r.description ?? "",
        rules_text: r.rules_text ?? "",
        reminder_text: (r as any).reminder_text ?? "",
        instruction: (r as any).instruction ?? "",
        player_guide: (r as any).player_guide ?? "",
        recommended_model: r.recommended_model ?? "",
        recommended_provider: (r as any).recommended_provider ?? "",
        dm_temperature: (r as any).dm_temperature ?? 0.3,
        dm_max_tokens: (r as any).dm_max_tokens ?? 2000,
        context_window: (r as any).context_window ?? 10,
        enforcement_config: (r as any).enforcement_config ?? { ...DEFAULT_ENFORCEMENT },
        stat_schema: r.stat_schema as StatDefinition[],
        is_public: r.is_public,
      });
    }).catch(() => navigate("/dm/configs/new"))
      .finally(() => setLoading(false));
  }, [configId, navigate]);

  function applyTemplate() {
    setForm({
      ...D20_TEMPLATE,
      description: D20_TEMPLATE.description ?? "",
      rules_text: D20_TEMPLATE.rules_text ?? "",
      reminder_text: D20_TEMPLATE.reminder_text ?? "",
      instruction: D20_TEMPLATE.instruction ?? "",
      player_guide: D20_TEMPLATE.player_guide ?? "",
      recommended_model: D20_TEMPLATE.recommended_model ?? "",
      recommended_provider: D20_TEMPLATE.recommended_provider ?? "",
    });
  }

  function updateStat(index: number, updated: StatDefinition) {
    setForm(f => ({ ...f, stat_schema: f.stat_schema.map((s, i) => i === index ? updated : s) }));
  }

  function removeStat(index: number) {
    setForm(f => ({ ...f, stat_schema: f.stat_schema.filter((_, i) => i !== index) }));
  }

  function moveStat(index: number, dir: -1 | 1) {
    setForm(f => {
      const arr = [...f.stat_schema];
      const target = index + dir;
      if (target < 0 || target >= arr.length) return f;
      [arr[index], arr[target]] = [arr[target], arr[index]];
      return { ...f, stat_schema: arr };
    });
  }

  function addStat() {
    setForm(f => ({ ...f, stat_schema: [...f.stat_schema, { ...BLANK_STAT }] }));
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      // The Ruleset API declares these optional fields as `string | null`, not
      // `string | undefined`. Coerce empty strings to null so the payload
      // matches the interface — sending undefined would type-check against a
      // partial body but fail the create endpoint, which expects null.
      const payload = {
        ...form,
        description: form.description || null,
        rules_text: form.rules_text || null,
        reminder_text: form.reminder_text || null,
        instruction: form.instruction || null,
        player_guide: form.player_guide || null,
        recommended_model: form.recommended_model || null,
        recommended_provider: form.recommended_provider || null,
      };
      if (isEdit && configId) {
        await dmApi.updateRuleset(configId, payload);
      } else {
        await dmApi.createRuleset(payload);
      }
      navigate("/chats");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to save");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete() {
    if (!configId || !confirm("Delete this ruleset? This cannot be undone.")) return;
    try {
      await dmApi.deleteRuleset(configId);
      navigate("/chats");
    } catch {
      setError("Failed to delete");
    }
  }

  if (loading) return <div className="flex h-full items-center justify-center text-gray-500">Loading…</div>;

  const tabs = [
    { id: "basic", label: "Basic" },
    { id: "rules", label: "Rules" },
    { id: "stats", label: `Stats (${form.stat_schema.length})` },
    { id: "model", label: "Model" },
    { id: "enforcement", label: "Enforcement" },
  ] as const;

  return (
    <div className="h-full overflow-y-auto p-6">
      <div className="max-w-3xl mx-auto">
        <div className="flex items-center justify-between mb-6">
          <h1 className="text-xl font-semibold">{isEdit ? "Edit Ruleset" : "New Ruleset"}</h1>
          <div className="flex gap-2">
            <button type="button" onClick={applyTemplate} className="px-3 py-1.5 rounded-lg border border-gray-700 text-sm text-gray-400 hover:text-gray-200 hover:border-gray-500 transition-colors">
              Use d20 Template
            </button>
            {isEdit && (
              <button type="button" onClick={handleDelete} className="px-3 py-1.5 rounded-lg bg-red-900/30 border border-red-800 text-sm text-red-400 hover:text-red-300 transition-colors">
                Delete
              </button>
            )}
          </div>
        </div>

        {/* Tabs */}
        <div className="flex gap-1 mb-4 border-b border-gray-800 pb-1">
          {tabs.map(t => (
            <button key={t.id} type="button" onClick={() => setActiveTab(t.id)}
              className={`px-3 py-1.5 text-sm rounded-t transition-colors ${activeTab === t.id ? "text-indigo-400 border-b-2 border-indigo-400" : "text-gray-500 hover:text-gray-300"}`}>
              {t.label}
            </button>
          ))}
        </div>

        <form onSubmit={handleSave} className="space-y-4">
          {activeTab === "basic" && (
            <div className="space-y-4">
              <Field label="Name *">
                <input required value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))} maxLength={255} className={inputCls} />
              </Field>
              <Field label="Description">
                <input value={form.description} onChange={e => setForm(f => ({ ...f, description: e.target.value }))} className={inputCls} />
              </Field>
              <Field label="Instruction (shown to narrative AI)">
                <textarea value={form.instruction} onChange={e => setForm(f => ({ ...f, instruction: e.target.value }))} rows={2} className={textareaCls} />
              </Field>
              <Field label="Player Guide (markdown, shown in chat)">
                <textarea value={form.player_guide} onChange={e => setForm(f => ({ ...f, player_guide: e.target.value }))} rows={4} className={textareaCls} />
              </Field>
              <label className="flex items-center gap-2 text-sm text-gray-400 cursor-pointer">
                <input type="checkbox" checked={form.is_public} onChange={e => setForm(f => ({ ...f, is_public: e.target.checked }))} />
                Public (visible to all users)
              </label>
            </div>
          )}

          {activeTab === "rules" && (
            <div className="space-y-4">
              <Field label="Game Rules (full ruleset, cached for performance)">
                <textarea value={form.rules_text} onChange={e => setForm(f => ({ ...f, rules_text: e.target.value }))} rows={12} className={`${textareaCls} font-mono text-xs`} placeholder="Describe your game rules in natural language…" />
                <p className="text-xs text-gray-600 mt-1">{Math.round(form.rules_text.length / 4)} est. tokens</p>
              </Field>
              <Field label="Critical Reminders (≤500 tokens, injected last for recency bias)">
                <textarea value={form.reminder_text} onChange={e => setForm(f => ({ ...f, reminder_text: e.target.value }))} rows={5} className={`${textareaCls} font-mono text-xs`} placeholder="Most critical rules the DM must never forget…" />
                <p className={`text-xs mt-1 ${Math.round(form.reminder_text.length / 4) > 500 ? "text-red-400" : "text-gray-600"}`}>
                  {Math.round(form.reminder_text.length / 4)} / 500 est. tokens
                </p>
              </Field>
            </div>
          )}

          {activeTab === "stats" && (
            <div className="space-y-3">
              <p className="text-xs text-gray-500">Each stat drives a typed parameter in the DM's tool interface.</p>
              {form.stat_schema.map((stat, i) => (
                <StatRow key={i} stat={stat} index={i} total={form.stat_schema.length}
                  onChange={s => updateStat(i, s)}
                  onRemove={() => removeStat(i)}
                  onMove={dir => moveStat(i, dir)}
                />
              ))}
              <button type="button" onClick={addStat} className="w-full py-2 rounded-lg border border-dashed border-gray-700 text-sm text-gray-500 hover:text-gray-300 hover:border-gray-500 transition-colors">
                + Add Stat
              </button>
            </div>
          )}

          {activeTab === "model" && (
            <div className="space-y-4">
              <Field label="DM Model">
                <input value={form.recommended_model} onChange={e => setForm(f => ({ ...f, recommended_model: e.target.value }))} placeholder="claude-haiku-4-5-20251001" className={inputCls} />
              </Field>
              <Field label="Provider">
                <select value={form.recommended_provider} onChange={e => setForm(f => ({ ...f, recommended_provider: e.target.value }))} className={inputCls}>
                  <option value="anthropic">Anthropic</option>
                  <option value="openrouter">OpenRouter</option>
                  <option value="google">Google</option>
                  <option value="openai">OpenAI</option>
                </select>
              </Field>
              <Field label={`Temperature: ${form.dm_temperature}`}>
                <input type="range" min="0" max="2" step="0.05" value={form.dm_temperature} onChange={e => setForm(f => ({ ...f, dm_temperature: Number(e.target.value) }))} className="w-full accent-indigo-500" />
                <div className="flex justify-between text-xs text-gray-600 mt-0.5"><span>Consistent (0)</span><span>Creative (2)</span></div>
              </Field>
              <div className="grid grid-cols-2 gap-4">
                <Field label="Max Tokens">
                  <input type="number" value={form.dm_max_tokens} onChange={e => setForm(f => ({ ...f, dm_max_tokens: Number(e.target.value) }))} min={256} max={8192} className={inputCls} />
                </Field>
                <Field label="Context Window (messages)">
                  <input type="number" value={form.context_window} onChange={e => setForm(f => ({ ...f, context_window: Number(e.target.value) }))} min={1} max={50} className={inputCls} />
                </Field>
              </div>
            </div>
          )}

          {activeTab === "enforcement" && (
            <div className="space-y-3">
              <p className="text-xs text-gray-500">Python-level hard checks that run before any LLM call. Disabled toggles let the DM model decide.</p>
              {(Object.entries(ENFORCEMENT_LABELS) as [keyof EnforcementConfig, string][]).map(([key, label]) => (
                <label key={key} className="flex items-center gap-3 py-2 border-b border-gray-800 cursor-pointer group">
                  <input type="checkbox" checked={form.enforcement_config[key]}
                    onChange={e => setForm(f => ({ ...f, enforcement_config: { ...f.enforcement_config, [key]: e.target.checked } }))}
                    className="w-4 h-4 accent-indigo-500" />
                  <span className="text-sm text-gray-300 group-hover:text-gray-100">{label}</span>
                </label>
              ))}
            </div>
          )}

          {error && <p className="text-sm text-red-400">{error}</p>}

          <div className="flex gap-3 pt-2">
            <button type="button" onClick={() => navigate(-1)} className="flex-1 py-2 rounded-lg border border-gray-700 text-sm text-gray-400 hover:text-gray-200 transition-colors">
              Cancel
            </button>
            <button type="submit" disabled={saving} className="flex-1 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-sm text-white font-medium transition-colors disabled:opacity-50">
              {saving ? "Saving…" : isEdit ? "Save Changes" : "Create Ruleset"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// ── Helper components ─────────────────────────────────────────────────────────

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-sm text-gray-400 mb-1">{label}</label>
      {children}
    </div>
  );
}

const inputCls = "w-full px-3 py-2 rounded-lg bg-gray-800 border border-gray-700 text-gray-100 focus:outline-none focus:border-indigo-500 text-sm";
const textareaCls = "w-full px-3 py-2 rounded-lg bg-gray-800 border border-gray-700 text-gray-100 focus:outline-none focus:border-indigo-500 text-sm resize-none";
