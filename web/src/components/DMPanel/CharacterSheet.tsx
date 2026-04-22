/**
 * CharacterSheet — displays and auto-styles stats from the DM session.
 *
 * Auto-recognition (case-insensitive):
 *   hp / health / hit_points  → red health bar
 *   mp / mana / magic_points  → blue mana bar
 *   stamina / energy          → yellow stamina bar
 *   Other number stats        → plain numeric display
 *   Text/enum stats           → badge
 */

import type { StatDefinition } from "../../api/dm";

interface Props {
  stats: Record<string, number | string>;
  schema: StatDefinition[];
  displayName?: string;
  deathState?: boolean;
}

interface BarConfig {
  colorClass: string;
  bgClass: string;
}

const STAT_BARS: Record<string, BarConfig> = {
  hp: { colorClass: "bg-red-500", bgClass: "bg-red-900/40" },
  health: { colorClass: "bg-red-500", bgClass: "bg-red-900/40" },
  hit_points: { colorClass: "bg-red-500", bgClass: "bg-red-900/40" },
  hitpoints: { colorClass: "bg-red-500", bgClass: "bg-red-900/40" },
  mp: { colorClass: "bg-blue-500", bgClass: "bg-blue-900/40" },
  mana: { colorClass: "bg-blue-500", bgClass: "bg-blue-900/40" },
  magic_points: { colorClass: "bg-blue-500", bgClass: "bg-blue-900/40" },
  stamina: { colorClass: "bg-yellow-500", bgClass: "bg-yellow-900/40" },
  energy: { colorClass: "bg-yellow-500", bgClass: "bg-yellow-900/40" },
};

function StatBar({
  value,
  max,
  config,
}: {
  value: number;
  max: number;
  config: BarConfig;
}) {
  const pct = max > 0 ? Math.max(0, Math.min(100, (value / max) * 100)) : 0;
  return (
    <div className={`h-2 rounded-full ${config.bgClass} overflow-hidden`}>
      <div
        className={`h-full rounded-full transition-all duration-500 ${config.colorClass}`}
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}

export function CharacterSheet({ stats, schema, displayName, deathState }: Props) {
  const schemaMap = Object.fromEntries(schema.map((s) => [s.name, s]));

  return (
    <div className="space-y-3">
      {displayName && (
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-gray-200">{displayName}</span>
          {deathState && (
            <span className="text-xs px-1.5 py-0.5 rounded bg-red-900 text-red-300 font-bold">
              FALLEN
            </span>
          )}
        </div>
      )}

      {Object.entries(stats).map(([name, value]) => {
        const def = schemaMap[name];
        const displayLabel = def?.display_name ?? name;
        const barConfig = STAT_BARS[name.toLowerCase()];
        const max = def?.max;
        const isNumber = typeof value === "number";

        return (
          <div key={name} className="space-y-0.5">
            <div className="flex justify-between items-center">
              <span className="text-xs text-gray-400 capitalize">{displayLabel}</span>
              {isNumber && (
                <span className={`text-xs font-mono font-semibold ${deathState && name.toLowerCase() in STAT_BARS ? "text-red-400" : "text-gray-200"}`}>
                  {value}{max != null ? ` / ${max}` : ""}
                </span>
              )}
              {!isNumber && (
                <span className="text-xs px-1.5 py-0.5 rounded bg-gray-700 text-gray-300">
                  {String(value)}
                </span>
              )}
            </div>
            {isNumber && barConfig && max != null && (
              <StatBar value={value as number} max={max} config={barConfig} />
            )}
          </div>
        );
      })}

      {Object.keys(stats).length === 0 && (
        <p className="text-xs text-gray-500 italic">No stats defined</p>
      )}
    </div>
  );
}
