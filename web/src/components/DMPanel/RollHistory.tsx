/**
 * RollHistory — scrollable log of recent dice rolls with context labels.
 */

import type { DMRoll } from "../../store/dmStore";

interface Props {
  rolls: DMRoll[];
}

function formatRelativeTime(timestamp: number): string {
  const diff = Date.now() - timestamp;
  if (diff < 60_000) return "just now";
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  return `${Math.floor(diff / 3_600_000)}h ago`;
}

function getRollColor(total: number, expression: string): string {
  // Color-code d20 natural rolls
  if (expression.includes("d20") || expression === "1d20") {
    if (total === 20) return "text-yellow-400"; // natural 20
    if (total === 1) return "text-red-400";     // natural 1
    if (total >= 15) return "text-green-400";
    if (total <= 5) return "text-red-300";
  }
  return "text-indigo-300";
}

export function RollHistory({ rolls }: Props) {
  if (rolls.length === 0) {
    return <p className="text-xs text-gray-500 italic">No rolls yet</p>;
  }

  return (
    <div className="space-y-2 max-h-64 overflow-y-auto scrollbar-thin scrollbar-thumb-gray-700">
      {rolls.map((roll) => (
        <div
          key={roll.rollId}
          className="rounded bg-gray-800/60 px-2.5 py-2 border border-gray-700/50"
        >
          <div className="flex items-center justify-between">
            <span className="text-xs font-mono text-gray-400">{roll.expression}</span>
            <span className={`text-sm font-bold font-mono ${getRollColor(roll.total, roll.expression)}`}>
              {roll.total}
            </span>
          </div>

          <div className="text-xs font-mono text-gray-600 mt-0.5">
            [{roll.allRolls.join(", ")}]
            {roll.modifier !== 0 && (
              <span className="text-gray-500">
                {" "}{roll.modifier > 0 ? "+" : ""}{roll.modifier}
              </span>
            )}
            {roll.keptRolls.length !== roll.allRolls.length && (
              <span className="text-yellow-600 ml-1">
                → [{roll.keptRolls.join(", ")}]
              </span>
            )}
          </div>

          {roll.context && (
            <p className="text-xs text-gray-500 mt-0.5 italic truncate">{roll.context}</p>
          )}

          <p className="text-xs text-gray-700 mt-0.5">{formatRelativeTime(roll.timestamp)}</p>
        </div>
      ))}
    </div>
  );
}
