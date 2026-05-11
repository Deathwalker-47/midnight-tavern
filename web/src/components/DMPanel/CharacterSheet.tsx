/**
 * CharacterSheet — displays stats, inventory, spells, skills, and description.
 *
 * Sections (§6.1 of game engine reference):
 *   1. Name + ALIVE/FALLEN badge
 *   2. Stats with resource bars
 *   3. Description (blue left-border, anti-drift anchor)
 *   4. Spells (grouped by Circle, purple accent)
 *
 * Bar color priority:
 *   1. bar_color hex from stat schema definition (if is_resource_bar)
 *   2. Auto-recognition by stat name (hp/mp/stamina/energy)
 *   3. Plain numeric display (no bar)
 */

import type { StatDefinition, SpellEntry } from "../../api/dm";

interface Props {
  stats: Record<string, number | string>;
  schema: StatDefinition[];
  displayName?: string;
  description?: string | null;
  spells?: SpellEntry[];
  /** Set when a death event fires in the current SSE stream. */
  deathState?: boolean;
  /** Set from the loaded character sheet's is_alive field. */
  isAlive?: boolean;
}

// Fallback bar colors for well-known stat names (when bar_color is not set)
const KNOWN_BAR_COLORS: Record<string, { fg: string; bg: string }> = {
  hp: { fg: "#ef4444", bg: "rgba(127,29,29,0.4)" },
  health: { fg: "#ef4444", bg: "rgba(127,29,29,0.4)" },
  hit_points: { fg: "#ef4444", bg: "rgba(127,29,29,0.4)" },
  hitpoints: { fg: "#ef4444", bg: "rgba(127,29,29,0.4)" },
  mp: { fg: "#3b82f6", bg: "rgba(30,58,138,0.4)" },
  mana: { fg: "#3b82f6", bg: "rgba(30,58,138,0.4)" },
  magic_points: { fg: "#3b82f6", bg: "rgba(30,58,138,0.4)" },
  stamina: { fg: "#eab308", bg: "rgba(113,63,18,0.4)" },
  energy: { fg: "#eab308", bg: "rgba(113,63,18,0.4)" },
};

function StatBar({
  value,
  max,
  fgColor,
  bgColor,
}: {
  value: number;
  max: number;
  fgColor: string;
  bgColor: string;
}) {
  const pct = max > 0 ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;
  return (
    <div className="h-2 rounded-full overflow-hidden" style={{ backgroundColor: bgColor }}>
      <div
        className="h-full rounded-full transition-all duration-500"
        style={{ width: `${pct}%`, backgroundColor: fgColor }}
      />
    </div>
  );
}

function SpellCircleGroup({ circle, spells }: { circle: number; spells: SpellEntry[] }) {
  return (
    <div className="space-y-0.5">
      <p className="text-xs text-purple-400 font-semibold">Circle {circle}</p>
      {spells.map((spell) => (
        <div key={spell.name} className="flex items-center justify-between gap-2 text-xs">
          <span className={`truncate ${spell.prepared === false ? "text-gray-500" : "text-purple-200"}`}>
            {spell.name}
          </span>
          <div className="flex items-center gap-1.5 shrink-0">
            {spell.mp_cost != null && (
              <span className="text-blue-400 font-mono">{spell.mp_cost}MP</span>
            )}
            {spell.prepared === false && (
              <span className="text-gray-600 text-xs">●</span>
            )}
            {spell.prepared !== false && (
              <span className="text-purple-500 text-xs">●</span>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

export function CharacterSheet({
  stats,
  schema,
  displayName,
  description,
  spells,
  deathState,
  isAlive,
}: Props) {
  const schemaMap = Object.fromEntries(schema.map((s) => [s.name, s]));
  const fallen = deathState || isAlive === false;

  // Group spells by circle
  const spellsByCircle: Record<number, SpellEntry[]> = {};
  if (spells) {
    for (const spell of spells) {
      const circle = spell.circle ?? 0;
      if (!spellsByCircle[circle]) spellsByCircle[circle] = [];
      spellsByCircle[circle].push(spell);
    }
  }
  const circleKeys = Object.keys(spellsByCircle)
    .map(Number)
    .sort((a, b) => a - b);

  return (
    <div className="space-y-3">
      {/* Name + alive status */}
      {displayName && (
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-gray-200">{displayName}</span>
          {fallen ? (
            <span className="text-xs px-1.5 py-0.5 rounded bg-red-900 text-red-300 font-bold">
              FALLEN
            </span>
          ) : (
            <span className="inline-block w-2 h-2 rounded-full bg-green-500" title="Alive" />
          )}
        </div>
      )}

      {/* Stats */}
      {Object.entries(stats).map(([name, value]) => {
        const def = schemaMap[name];
        const displayLabel = def?.display_name ?? name;
        const isNumber = typeof value === "number";
        const max = def?.max;

        let barColors: { fg: string; bg: string } | null = null;
        if (isNumber && max != null) {
          if (def?.is_resource_bar && def.bar_color) {
            barColors = { fg: def.bar_color, bg: `${def.bar_color}33` };
          } else {
            barColors = KNOWN_BAR_COLORS[name.toLowerCase()] ?? null;
          }
        }

        return (
          <div key={name} className="space-y-0.5">
            <div className="flex justify-between items-center">
              <span className="text-xs text-gray-400 capitalize">{displayLabel}</span>
              {isNumber && (
                <span
                  className={`text-xs font-mono font-semibold ${
                    fallen && barColors ? "text-red-400" : "text-gray-200"
                  }`}
                >
                  {value}
                  {max != null ? ` / ${max}` : ""}
                </span>
              )}
              {!isNumber && (
                <span className="text-xs px-1.5 py-0.5 rounded bg-gray-700 text-gray-300">
                  {String(value)}
                </span>
              )}
            </div>
            {isNumber && barColors && max != null && (
              <StatBar
                value={value as number}
                max={max}
                fgColor={barColors.fg}
                bgColor={barColors.bg}
              />
            )}
          </div>
        );
      })}

      {Object.keys(stats).length === 0 && (
        <p className="text-xs text-gray-500 italic">No stats defined</p>
      )}

      {/* Description — anti-drift identity anchor (§3.4) */}
      {description && (
        <div className="border-l-2 border-blue-600 pl-2 mt-2">
          <p className="text-xs text-gray-400 font-semibold mb-0.5">📋 Description</p>
          <p className="text-xs text-blue-200 leading-relaxed whitespace-pre-wrap">{description}</p>
        </div>
      )}

      {/* Spells — grouped by circle (§3.5, §6.1) */}
      {circleKeys.length > 0 && (
        <div className="mt-2 space-y-2">
          <p className="text-xs text-gray-400 font-semibold">✨ Spells</p>
          {circleKeys.map((c) => (
            <SpellCircleGroup key={c} circle={c} spells={spellsByCircle[c]} />
          ))}
        </div>
      )}
    </div>
  );
}
