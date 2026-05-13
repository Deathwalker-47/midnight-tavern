/**
 * Read-only browse UI for the backdrop asset library.
 *
 * Backed by GET /api/v1/images/backdrops. Tag filter chips are built from
 * the union of tags returned by the API. Clicking a card opens a modal
 * showing the full metadata (slug, description, tags, lighting profile).
 *
 * Writes are out of scope — when admin delete is wanted, the backend needs
 * a DELETE endpoint first.
 */

import { useEffect, useMemo, useState } from "react";

import { imagesApi, type BackdropResponse } from "../api/images";

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

function BackdropCard({
  backdrop,
  onSelect,
}: {
  backdrop: BackdropResponse;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className="group block rounded-lg overflow-hidden border border-gray-800 bg-gray-900 hover:border-indigo-600 transition-colors text-left"
    >
      <div className="aspect-[3/2] bg-gray-950 overflow-hidden">
        <img
          src={backdrop.storage_url}
          alt={backdrop.description}
          loading="lazy"
          className="w-full h-full object-cover group-hover:scale-105 transition-transform"
        />
      </div>
      <div className="p-3 space-y-1">
        <div className="text-sm font-medium text-gray-200 truncate">
          {backdrop.slug}
        </div>
        <div className="text-xs text-gray-500 truncate">
          {backdrop.description}
        </div>
        <div className="flex flex-wrap gap-1 pt-1">
          {backdrop.tags.slice(0, 4).map((t) => (
            <span
              key={t}
              className="text-[10px] px-1.5 py-0.5 rounded bg-gray-800 text-gray-400"
            >
              {t}
            </span>
          ))}
        </div>
      </div>
    </button>
  );
}

function BackdropModal({
  backdrop,
  onClose,
}: {
  backdrop: BackdropResponse;
  onClose: () => void;
}) {
  return (
    <div
      className="fixed inset-0 bg-black/70 flex items-center justify-center p-4 z-50"
      onClick={onClose}
    >
      <div
        className="max-w-2xl w-full bg-gray-900 rounded-lg overflow-hidden border border-gray-800"
        onClick={(e) => e.stopPropagation()}
      >
        <img
          src={backdrop.storage_url}
          alt={backdrop.description}
          className="w-full aspect-[3/2] object-cover"
        />
        <div className="p-4 space-y-3">
          <div className="flex items-start justify-between gap-4">
            <h2 className="text-lg font-semibold text-gray-100">
              {backdrop.slug}
            </h2>
            <button
              onClick={onClose}
              className="text-gray-500 hover:text-gray-300 text-xl leading-none"
              aria-label="Close"
            >
              ×
            </button>
          </div>
          <p className="text-sm text-gray-400">{backdrop.description}</p>
          <div className="grid grid-cols-2 gap-3 text-xs">
            <div>
              <div className="text-gray-500 mb-1">Dimensions</div>
              <div className="text-gray-300">
                {backdrop.width} × {backdrop.height} ({backdrop.aspect_ratio})
              </div>
            </div>
            <div>
              <div className="text-gray-500 mb-1">Generated</div>
              <div className="text-gray-300">
                {new Date(backdrop.generated_at).toLocaleString()}
              </div>
            </div>
          </div>
          <div>
            <div className="text-xs text-gray-500 mb-1">Tags</div>
            <div className="flex flex-wrap gap-1">
              {backdrop.tags.map((t) => (
                <span
                  key={t}
                  className="text-[11px] px-2 py-0.5 rounded bg-gray-800 text-gray-300"
                >
                  {t}
                </span>
              ))}
            </div>
          </div>
          <div>
            <div className="text-xs text-gray-500 mb-1">Lighting profile</div>
            <pre className="text-[11px] bg-gray-950 text-gray-300 rounded p-2 overflow-x-auto">
              {JSON.stringify(backdrop.lighting_profile, null, 2)}
            </pre>
          </div>
        </div>
      </div>
    </div>
  );
}

export function BackdropsPage() {
  const [backdrops, setBackdrops] = useState<BackdropResponse[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeTags, setActiveTags] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<BackdropResponse | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    imagesApi
      .listBackdrops()
      .then((rows) => {
        if (!cancelled) {
          setBackdrops(rows);
          setError(null);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load backdrops");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Union of tags across all backdrops, lowercase for stable filter UX.
  const allTags = useMemo(() => {
    const s = new Set<string>();
    for (const b of backdrops) for (const t of b.tags) s.add(t.toLowerCase());
    return Array.from(s).sort();
  }, [backdrops]);

  const filtered = useMemo(() => {
    if (activeTags.size === 0) return backdrops;
    return backdrops.filter((b) => {
      const lowered = new Set(b.tags.map((t) => t.toLowerCase()));
      for (const t of activeTags) if (!lowered.has(t)) return false;
      return true;
    });
  }, [backdrops, activeTags]);

  function toggleTag(tag: string) {
    setActiveTags((prev) => {
      const next = new Set(prev);
      if (next.has(tag)) next.delete(tag);
      else next.add(tag);
      return next;
    });
  }

  return (
    <div className="h-full overflow-y-auto px-6 py-4">
      <div className="max-w-7xl mx-auto space-y-4">
        <div className="flex items-baseline justify-between">
          <h1 className="text-xl font-semibold text-gray-100">Backdrops</h1>
          <div className="text-sm text-gray-500">
            {filtered.length} of {backdrops.length}
          </div>
        </div>

        {allTags.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {allTags.map((t) => (
              <FilterChip
                key={t}
                label={t}
                active={activeTags.has(t)}
                onClick={() => toggleTag(t)}
              />
            ))}
            {activeTags.size > 0 && (
              <button
                type="button"
                onClick={() => setActiveTags(new Set())}
                className="text-xs px-2.5 py-1 rounded-full bg-gray-800 text-gray-500 hover:text-gray-300"
              >
                Clear
              </button>
            )}
          </div>
        )}

        {loading && (
          <div className="text-sm text-gray-500">Loading…</div>
        )}
        {error && (
          <div className="text-sm text-red-400 bg-red-950/30 px-3 py-2 rounded">
            {error}
          </div>
        )}
        {!loading && !error && filtered.length === 0 && (
          <div className="text-sm text-gray-500 italic">
            No backdrops match the current filters.
          </div>
        )}

        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3">
          {filtered.map((b) => (
            <BackdropCard
              key={b.id}
              backdrop={b}
              onSelect={() => setSelected(b)}
            />
          ))}
        </div>
      </div>

      {selected && (
        <BackdropModal backdrop={selected} onClose={() => setSelected(null)} />
      )}
    </div>
  );
}
