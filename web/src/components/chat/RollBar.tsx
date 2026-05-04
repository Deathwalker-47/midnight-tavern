/**
 * RollBar — rendered below an assistant message when a DM game event exists.
 * Shows dice rolls, stat changes, and the ruling verdict.
 * Collapsed by default when ruling is "continue" with no visible changes.
 */

import { useState } from "react";
import type { DMEvalResult, DMRoll, StatChange } from "../../store/dmStore";

interface Props {
  result: DMEvalResult;
  rolls: DMRoll[];
}

function RollEntry({ roll }: { roll: DMRoll }) {
  const diceStr = roll.allRolls.length > 0 ? `[${roll.allRolls.join(", ")}]` : "";
  const modStr =
    roll.modifier > 0 ? `+${roll.modifier}` : roll.modifier < 0 ? String(roll.modifier) : "";
  return (
    <div className="flex items-center gap-2 text-xs font-mono">
      {roll.context && (
        <span className="text-gray-400 not-italic truncate max-w-[180px]">{roll.context}</span>
      )}
      <span className="text-gray-500">{roll.expression}</span>
      {diceStr && <span className="text-gray-400">{diceStr}</span>}
      {modStr && <span className="text-gray-400">{modStr}</span>}
      <span className="text-gray-500">=</span>
      <span className="font-bold text-white">{roll.total}</span>
    </div>
  );
}

function StatChangeEntry({ change }: { change: StatChange }) {
  const { delta } = change;
  const isNegative = typeof delta === "number" && delta < 0;
  const isPositive = typeof delta === "number" && delta > 0;
  const deltaStr =
    delta != null ? (delta > 0 ? `+${delta}` : String(delta)) : "";

  return (
    <div className="flex items-center gap-1.5 text-xs">
      <span className="text-gray-400">{change.displayName}:</span>
      <span className="font-mono text-gray-300">{String(change.oldValue)}</span>
      <span className="text-gray-500">→</span>
      <span
        className={`font-mono font-semibold ${
          isNegative ? "text-red-400" : isPositive ? "text-green-400" : "text-gray-200"
        }`}
      >
        {String(change.newValue)}
      </span>
      {deltaStr && (
        <span
          className={`${
            isNegative ? "text-red-500" : isPositive ? "text-green-500" : "text-gray-500"
          }`}
        >
          ({deltaStr})
        </span>
      )}
    </div>
  );
}

export function RollBar({ result, rolls }: Props) {
  const linkedRolls = rolls.filter((r) => result.rollIds.includes(r.rollId));
  const hasContent =
    linkedRolls.length > 0 ||
    result.statChanges.length > 0 ||
    !!result.rejectionReason;

  const [collapsed, setCollapsed] = useState(
    result.verdict === "continue" && !hasContent,
  );

  // Nothing to show for a clean pass
  if (result.verdict === "continue" && !hasContent) return null;

  const borderColor =
    result.verdict === "reject"
      ? "border-red-800/50 bg-red-950/20 text-red-300"
      : result.verdict === "ask"
        ? "border-yellow-800/50 bg-yellow-950/20 text-yellow-300"
        : "border-indigo-800/50 bg-indigo-950/20 text-indigo-300";

  const label =
    result.verdict === "reject"
      ? "Action Rejected"
      : result.verdict === "ask"
        ? "DM Question"
        : result.summary || "DM Ruling";

  return (
    <div className={`mt-1 rounded-xl border px-3 py-2 text-xs ${borderColor}`}>
      <button
        onClick={() => setCollapsed((c) => !c)}
        className="flex w-full items-center gap-2 text-left"
      >
        <span>🎲</span>
        <span className="font-medium truncate">{label}</span>
        <span className="ml-auto opacity-60 shrink-0">{collapsed ? "▸" : "▾"}</span>
      </button>

      {!collapsed && (
        <div className="mt-2 space-y-1.5 border-t border-white/10 pt-2">
          {result.rejectionReason && (
            <p className="text-red-300">{result.rejectionReason}</p>
          )}
          {linkedRolls.map((roll) => (
            <RollEntry key={roll.rollId} roll={roll} />
          ))}
          {result.statChanges.map((change, i) => (
            <StatChangeEntry key={i} change={change} />
          ))}
          {result.summary && result.verdict !== "continue" && !result.rejectionReason && (
            <p className="italic text-gray-400">{result.summary}</p>
          )}
        </div>
      )}
    </div>
  );
}
