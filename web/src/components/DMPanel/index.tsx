/**
 * DMPanel — collapsible right sidebar for DM session management.
 *
 * Shows when a DM session is active. Contains:
 *   - Character sheet (stats, HP/MP/stamina bars)
 *   - Dice roller (quick-roll + custom expression)
 *   - Roll history (last 50 rolls)
 *
 * Receives ruleset schema from parent (fetched when session is created).
 */

import { useState } from "react";
import { useDMStore } from "../../store/dmStore";
import type { StatDefinition } from "../../api/dm";
import { CharacterSheet } from "./CharacterSheet";
import { DiceRoller } from "./DiceRoller";
import { RollHistory } from "./RollHistory";

interface Props {
  statSchema: StatDefinition[];
}

type Tab = "sheet" | "dice" | "history";

export function DMPanel({ statSchema }: Props) {
  const [activeTab, setActiveTab] = useState<Tab>("sheet");
  const [collapsed, setCollapsed] = useState(false);

  const {
    sessionId,
    isActive,
    stats,
    inventory,
    skills,
    rolls,
    isEvaluating,
    isAlive,
    pendingQuestion,
    lastRejection,
    deathState,
    clearEvalState,
  } = useDMStore();

  if (!isActive || !sessionId) return null;

  const tabs: { id: Tab; label: string }[] = [
    { id: "sheet", label: "Sheet" },
    { id: "dice", label: "Dice" },
    { id: "history", label: `History${rolls.length > 0 ? ` (${rolls.length})` : ""}` },
  ];

  return (
    <aside className={`flex flex-col border-l border-gray-800 bg-gray-900 transition-all duration-200 ${collapsed ? "w-10" : "w-64"}`}>
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-gray-800">
        {!collapsed && (
          <div className="flex items-center gap-2">
            <span className="text-xs font-semibold text-gray-300 uppercase tracking-wider">⚔ DM</span>
            {isEvaluating && (
              <span className="flex items-center gap-1 text-xs text-indigo-400">
                <span className="animate-pulse">●</span> evaluating
              </span>
            )}
          </div>
        )}
        <button
          onClick={() => setCollapsed((v) => !v)}
          className="text-gray-500 hover:text-gray-300 transition-colors ml-auto"
          title={collapsed ? "Expand DM Panel" : "Collapse DM Panel"}
        >
          {collapsed ? "›" : "‹"}
        </button>
      </div>

      {!collapsed && (
        <>
          {/* Status banners */}
          {lastRejection && (
            <div className="mx-2 mt-2 rounded bg-red-900/40 border border-red-700/50 px-3 py-2">
              <div className="flex items-start justify-between gap-1">
                <div>
                  <p className="text-xs font-semibold text-red-400">Action Rejected</p>
                  <p className="text-xs text-red-300 mt-0.5">{lastRejection}</p>
                </div>
                <button onClick={clearEvalState} className="text-red-600 hover:text-red-400 text-xs mt-0.5">✕</button>
              </div>
            </div>
          )}

          {pendingQuestion && (
            <div className="mx-2 mt-2 rounded bg-yellow-900/30 border border-yellow-700/50 px-3 py-2">
              <p className="text-xs font-semibold text-yellow-400">DM Question</p>
              <p className="text-xs text-yellow-300 mt-0.5">{pendingQuestion}</p>
            </div>
          )}

          {deathState && (
            <div className="mx-2 mt-2 rounded bg-red-950 border border-red-800 px-3 py-2">
              <p className="text-xs font-bold text-red-300 text-center">☠ CHARACTER FALLEN</p>
            </div>
          )}

          {/* Tabs */}
          <div className="flex border-b border-gray-800">
            {tabs.map((tab) => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`flex-1 py-2 text-xs transition-colors ${
                  activeTab === tab.id
                    ? "text-indigo-400 border-b-2 border-indigo-500 font-medium"
                    : "text-gray-500 hover:text-gray-300"
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>

          {/* Tab content */}
          <div className="flex-1 overflow-y-auto p-3">
            {activeTab === "sheet" && (
              <CharacterSheet
                stats={stats}
                schema={statSchema}
                deathState={deathState}
                isAlive={isAlive}
              />
            )}
            {activeTab === "dice" && (
              <DiceRoller sessionId={sessionId} />
            )}
            {activeTab === "history" && (
              <RollHistory rolls={rolls} />
            )}
          </div>

          {/* Inventory (always visible at bottom of sheet tab) */}
          {activeTab === "sheet" && inventory.length > 0 && (
            <div className="border-t border-gray-800 p-3">
              <p className="text-xs text-gray-400 font-semibold mb-1.5">Inventory</p>
              <ul className="space-y-0.5">
                {inventory.map((item, i) => (
                  <li key={i} className="text-xs text-gray-300">• {item}</li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </aside>
  );
}
