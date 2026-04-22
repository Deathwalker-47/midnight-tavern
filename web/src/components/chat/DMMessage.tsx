/**
 * DMMessage — visually distinct chat bubble for DM events.
 *
 * Renders differently based on the DM event type:
 *   roll result   → dice card with breakdown
 *   stat update   → stat change diff badges
 *   rejection     → red rejection banner
 *   ask           → yellow question bubble
 *   submit/done   → DM ruling text in dark bordered box
 */

interface StatDiff {
  name: string;
  displayName: string;
  oldValue: number | string;
  newValue: number | string;
  delta: number | null;
}

interface RollDisplay {
  expression: string;
  allRolls: number[];
  keptRolls: number[];
  modifier: number;
  total: number;
  context?: string;
}

export type DMMessageType =
  | { kind: "ruling"; text: string }
  | { kind: "roll"; roll: RollDisplay }
  | { kind: "stat_update"; changes: StatDiff[]; deathState?: boolean }
  | { kind: "rejection"; reason: string }
  | { kind: "ask"; question: string };

interface Props {
  message: DMMessageType;
}

function DiceCard({ roll }: { roll: RollDisplay }) {
  const isNat20 = roll.expression.toLowerCase().includes("d20") && roll.total === 20;
  const isNat1 = roll.expression.toLowerCase().includes("d20") && roll.total === 1 && roll.modifier === 0;

  return (
    <div className={`rounded-lg border px-3 py-2.5 space-y-1.5 ${
      isNat20 ? "border-yellow-600/60 bg-yellow-950/40" :
      isNat1  ? "border-red-700/60 bg-red-950/40" :
                "border-gray-700 bg-gray-800/60"
    }`}>
      <div className="flex items-center justify-between">
        <span className="text-xs font-mono text-gray-400">{roll.expression}</span>
        <span className={`text-xl font-bold font-mono leading-none ${
          isNat20 ? "text-yellow-400" :
          isNat1  ? "text-red-400" :
                    "text-indigo-300"
        }`}>
          {roll.total}
          {isNat20 && <span className="text-xs ml-1">✦ Nat 20!</span>}
          {isNat1  && <span className="text-xs ml-1 text-red-500">Nat 1</span>}
        </span>
      </div>

      <div className="text-xs font-mono text-gray-600">
        [{roll.allRolls.join(", ")}]
        {roll.modifier !== 0 && (
          <span className="text-gray-500"> {roll.modifier > 0 ? "+" : ""}{roll.modifier}</span>
        )}
        {roll.keptRolls.length !== roll.allRolls.length && (
          <span className="text-yellow-600 ml-1">→ kept [{roll.keptRolls.join(", ")}]</span>
        )}
      </div>

      {roll.context && (
        <p className="text-xs text-gray-500 italic">{roll.context}</p>
      )}
    </div>
  );
}

function StatUpdateCard({ changes, deathState }: { changes: StatDiff[]; deathState?: boolean }) {
  return (
    <div className="space-y-1.5">
      {deathState && (
        <div className="rounded bg-red-950 border border-red-800 px-2.5 py-1.5 text-center">
          <span className="text-xs font-bold text-red-300">☠ Character Fallen</span>
        </div>
      )}
      {changes.map((c) => {
        const isNumeric = typeof c.delta === "number";
        const decreased = isNumeric && (c.delta as number) < 0;
        const increased = isNumeric && (c.delta as number) > 0;

        return (
          <div key={c.name} className="flex items-center justify-between rounded bg-gray-800/50 border border-gray-700/50 px-2.5 py-1.5">
            <span className="text-xs text-gray-400">{c.displayName}</span>
            <div className="flex items-center gap-1.5 text-xs font-mono">
              <span className="text-gray-500">{String(c.oldValue)}</span>
              <span className="text-gray-600">→</span>
              <span className={decreased ? "text-red-400" : increased ? "text-green-400" : "text-gray-300"}>
                {String(c.newValue)}
              </span>
              {isNumeric && c.delta !== 0 && (
                <span className={`text-xs ${decreased ? "text-red-600" : "text-green-600"}`}>
                  ({c.delta! > 0 ? "+" : ""}{c.delta})
                </span>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

export function DMMessage({ message }: Props) {
  return (
    <div className="flex gap-2 py-1">
      {/* DM avatar indicator */}
      <div className="flex-shrink-0 w-6 h-6 rounded-full bg-indigo-900 border border-indigo-700 flex items-center justify-center mt-0.5">
        <span className="text-xs">⚔</span>
      </div>

      <div className="flex-1 min-w-0 space-y-1.5">
        <span className="text-xs font-semibold text-indigo-400">Dungeon Master</span>

        {message.kind === "ruling" && (
          <div className="rounded-lg border border-indigo-900/60 bg-indigo-950/30 px-3 py-2">
            <p className="text-sm text-gray-300">{message.text}</p>
          </div>
        )}

        {message.kind === "roll" && <DiceCard roll={message.roll} />}

        {message.kind === "stat_update" && (
          <StatUpdateCard changes={message.changes} deathState={message.deathState} />
        )}

        {message.kind === "rejection" && (
          <div className="rounded-lg border border-red-800/60 bg-red-950/30 px-3 py-2">
            <p className="text-xs font-semibold text-red-400 mb-0.5">Action Not Allowed</p>
            <p className="text-sm text-red-300">{message.reason}</p>
          </div>
        )}

        {message.kind === "ask" && (
          <div className="rounded-lg border border-yellow-800/60 bg-yellow-950/30 px-3 py-2">
            <p className="text-xs font-semibold text-yellow-400 mb-0.5">DM asks:</p>
            <p className="text-sm text-yellow-200">{message.question}</p>
          </div>
        )}
      </div>
    </div>
  );
}
