/**
 * DiceRoller — manual dice roll UI for player-initiated rolls.
 * Supports quick-roll buttons (d4, d6, d8, d10, d12, d20, d100)
 * and a free-text expression input.
 */

import { useState } from "react";
import { dmApi, type RollResult } from "../../api/dm";

interface Props {
  sessionId: string;
  onRoll?: (result: RollResult) => void;
}

const QUICK_ROLLS = ["d4", "d6", "d8", "d10", "d12", "d20", "d100"];

export function DiceRoller({ sessionId, onRoll }: Props) {
  const [expression, setExpression] = useState("");
  const [context, setContext] = useState("");
  const [isRolling, setIsRolling] = useState(false);
  const [lastResult, setLastResult] = useState<RollResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function roll(expr: string) {
    setIsRolling(true);
    setError(null);
    try {
      const result = await dmApi.roll(sessionId, expr, context || undefined);
      setLastResult(result);
      onRoll?.(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Roll failed");
    } finally {
      setIsRolling(false);
    }
  }

  return (
    <div className="space-y-3">
      {/* Quick roll buttons */}
      <div className="flex flex-wrap gap-1.5">
        {QUICK_ROLLS.map((die) => (
          <button
            key={die}
            onClick={() => roll(die)}
            disabled={isRolling}
            className="px-2 py-1 text-xs font-mono rounded bg-gray-700 hover:bg-indigo-700 text-gray-200 transition-colors disabled:opacity-50"
          >
            {die}
          </button>
        ))}
      </div>

      {/* Custom expression */}
      <div className="flex gap-2">
        <input
          type="text"
          value={expression}
          onChange={(e) => setExpression(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && expression && roll(expression)}
          placeholder="2d6+3"
          className="flex-1 px-2 py-1.5 text-xs font-mono rounded bg-gray-800 border border-gray-700 text-gray-200 placeholder-gray-500 focus:outline-none focus:border-indigo-500"
        />
        <button
          onClick={() => expression && roll(expression)}
          disabled={isRolling || !expression}
          className="px-3 py-1.5 text-xs rounded bg-indigo-600 hover:bg-indigo-500 text-white transition-colors disabled:opacity-50"
        >
          Roll
        </button>
      </div>

      <input
        type="text"
        value={context}
        onChange={(e) => setContext(e.target.value)}
        placeholder="Context (optional)"
        className="w-full px-2 py-1.5 text-xs rounded bg-gray-800 border border-gray-700 text-gray-200 placeholder-gray-500 focus:outline-none focus:border-indigo-500"
      />

      {error && <p className="text-xs text-red-400">{error}</p>}

      {/* Last result display */}
      {lastResult && (
        <div className="rounded-md bg-gray-800 border border-gray-700 p-3 space-y-1">
          <div className="flex items-center justify-between">
            <span className="text-xs text-gray-400 font-mono">{lastResult.expression}</span>
            <span className="text-lg font-bold font-mono text-indigo-300">
              {lastResult.total}
            </span>
          </div>
          <div className="text-xs text-gray-500 font-mono">
            [{lastResult.all_rolls.join(", ")}]
            {lastResult.modifier !== 0 && (
              <span className="text-gray-400">
                {" "}{lastResult.modifier > 0 ? "+" : ""}{lastResult.modifier}
              </span>
            )}
            {lastResult.kept_rolls.length !== lastResult.all_rolls.length && (
              <span className="text-yellow-500 ml-1">
                → kept [{lastResult.kept_rolls.join(", ")}]
              </span>
            )}
          </div>
          {lastResult.context && (
            <p className="text-xs text-gray-500 italic">{lastResult.context}</p>
          )}
        </div>
      )}
    </div>
  );
}
