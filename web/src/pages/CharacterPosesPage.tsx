/**
 * Read-only browse UI for a character's pose library.
 *
 * Backed by GET /api/v1/images/characters/{characterId}/poses. Filter chips
 * cover pose-slug, expression, facing, and lora-version axes. Transparent
 * PNGs are rendered over a checker background so alpha shows clearly.
 *
 * Writes are out of scope.
 */

import { useEffect, useMemo, useState } from "react";
import { useParams, Link } from "react-router-dom";

import { charactersApi, type Character } from "../api/characters";
import { imagesApi, type CharacterPoseResponse } from "../api/images";

type Axis = "pose_slug" | "expression_slug" | "facing" | "lora_version";

const AXIS_LABEL: Record<Axis, string> = {
  pose_slug: "Pose",
  expression_slug: "Expression",
  facing: "Facing",
  lora_version: "LoRA version",
};

function FilterChip({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`text-xs px-2.5 py-1 rounded-full transition-colors ${
        active
          ? "bg-indigo-600 text-white"
          : "bg-gray-800 text-gray-400 hover:text-gray-200 hover:bg-gray-700"
      }`}
    >
      {label}
    </button>
  );
}

function AxisFilters({
  axis,
  values,
  active,
  onToggle,
}: {
  axis: Axis;
  values: string[];
  active: string | null;
  onToggle: (v: string) => void;
}) {
  if (values.length === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="text-xs text-gray-500 mr-1 min-w-[80px]">
        {AXIS_LABEL[axis]}
      </span>
      {values.map((v) => (
        <FilterChip
          key={v}
          label={v}
          active={active === v}
          onClick={() => onToggle(v)}
        />
      ))}
    </div>
  );
}

function PoseCard({ pose }: { pose: CharacterPoseResponse }) {
  return (
    <div className="rounded-lg overflow-hidden border border-gray-800 bg-gray-900">
      {/* Checker background for the transparent pose PNG */}
      <div
        className="aspect-[3/4] bg-gray-950"
        style={{
          backgroundImage:
            "linear-gradient(45deg, #1f2937 25%, transparent 25%), linear-gradient(-45deg, #1f2937 25%, transparent 25%), linear-gradient(45deg, transparent 75%, #1f2937 75%), linear-gradient(-45deg, transparent 75%, #1f2937 75%)",
          backgroundSize: "20px 20px",
          backgroundPosition: "0 0, 0 10px, 10px -10px, -10px 0px",
        }}
      >
        <img
          src={pose.transparent_storage_url}
          alt={`${pose.pose_slug} / ${pose.expression_slug}`}
          loading="lazy"
          className="w-full h-full object-contain"
        />
      </div>
      <div className="p-2 space-y-1 text-xs">
        <div className="font-medium text-gray-200 truncate">
          {pose.pose_slug}
        </div>
        <div className="flex flex-wrap gap-1 text-[10px]">
          <span className="px-1.5 py-0.5 rounded bg-gray-800 text-gray-400">
            {pose.expression_slug}
          </span>
          <span className="px-1.5 py-0.5 rounded bg-gray-800 text-gray-400">
            {pose.facing}
          </span>
          <span className="px-1.5 py-0.5 rounded bg-gray-800 text-gray-500">
            {pose.lora_version}
          </span>
        </div>
      </div>
    </div>
  );
}

export function CharacterPosesPage() {
  const { characterId } = useParams<{ characterId: string }>();
  const [character, setCharacter] = useState<Character | null>(null);
  const [poses, setPoses] = useState<CharacterPoseResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filters, setFilters] = useState<Record<Axis, string | null>>({
    pose_slug: null,
    expression_slug: null,
    facing: null,
    lora_version: null,
  });

  useEffect(() => {
    if (!characterId) return;
    let cancelled = false;
    setLoading(true);
    Promise.all([
      charactersApi.get(characterId).catch(() => null),
      imagesApi.listPoses(characterId),
    ])
      .then(([char, posesRows]) => {
        if (cancelled) return;
        setCharacter(char);
        setPoses(posesRows);
        setError(null);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load poses");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [characterId]);

  const axisValues = useMemo(() => {
    const collect = (key: Axis): string[] =>
      Array.from(new Set(poses.map((p) => p[key]))).sort();
    return {
      pose_slug: collect("pose_slug"),
      expression_slug: collect("expression_slug"),
      facing: collect("facing"),
      lora_version: collect("lora_version"),
    };
  }, [poses]);

  const filtered = useMemo(() => {
    return poses.filter((p) => {
      for (const axis of ["pose_slug", "expression_slug", "facing", "lora_version"] as Axis[]) {
        const v = filters[axis];
        if (v !== null && p[axis] !== v) return false;
      }
      return true;
    });
  }, [poses, filters]);

  function toggleFilter(axis: Axis, value: string) {
    setFilters((prev) => ({
      ...prev,
      [axis]: prev[axis] === value ? null : value,
    }));
  }

  return (
    <div className="h-full overflow-y-auto px-6 py-4">
      <div className="max-w-7xl mx-auto space-y-4">
        <div className="flex items-baseline justify-between">
          <div>
            <Link to="/characters" className="text-xs text-gray-500 hover:text-gray-300">
              ← Characters
            </Link>
            <h1 className="text-xl font-semibold text-gray-100 mt-1">
              {character?.name ?? "Character"} — Poses
            </h1>
          </div>
          <div className="text-sm text-gray-500">
            {filtered.length} of {poses.length}
          </div>
        </div>

        {!loading && poses.length > 0 && (
          <div className="space-y-1.5">
            {(["pose_slug", "expression_slug", "facing", "lora_version"] as Axis[]).map(
              (axis) => (
                <AxisFilters
                  key={axis}
                  axis={axis}
                  values={axisValues[axis]}
                  active={filters[axis]}
                  onToggle={(v) => toggleFilter(axis, v)}
                />
              ),
            )}
          </div>
        )}

        {loading && <div className="text-sm text-gray-500">Loading…</div>}
        {error && (
          <div className="text-sm text-red-400 bg-red-950/30 px-3 py-2 rounded">
            {error}
          </div>
        )}
        {!loading && !error && poses.length === 0 && (
          <div className="text-sm text-gray-500 italic">
            No poses have been generated for this character yet.
          </div>
        )}
        {!loading && !error && poses.length > 0 && filtered.length === 0 && (
          <div className="text-sm text-gray-500 italic">
            No poses match the current filters.
          </div>
        )}

        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-3">
          {filtered.map((p) => (
            <PoseCard key={p.id} pose={p} />
          ))}
        </div>
      </div>
    </div>
  );
}
