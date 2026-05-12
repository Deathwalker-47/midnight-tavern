/**
 * Characters page — list user's characters and create new ones.
 */

import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { charactersApi, type Character, type CreateCharacterBody } from "../api/characters";
import { chatsApi } from "../api/chats";
import { ApiError } from "../api/client";

function CharacterCard({
  character,
  onChat,
}: {
  character: Character;
  onChat: (id: string) => void;
}) {
  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900 p-4 flex flex-col gap-3 hover:border-gray-700 transition-colors">
      <div className="flex items-start gap-3">
        <div className="w-12 h-12 rounded-full bg-indigo-900 border border-indigo-700 flex items-center justify-center text-xl flex-shrink-0">
          {character.avatar_url ? (
            <img src={character.avatar_url} alt={character.name} className="w-full h-full rounded-full object-cover" />
          ) : (
            character.name[0].toUpperCase()
          )}
        </div>
        <div className="flex-1 min-w-0">
          <h3 className="font-semibold text-gray-100 truncate">{character.name}</h3>
          {character.description && (
            <p className="text-sm text-gray-400 mt-0.5 line-clamp-2">{character.description}</p>
          )}
        </div>
      </div>
      {character.tags.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {character.tags.map((tag) => (
            <span key={tag} className="text-xs px-2 py-0.5 rounded-full bg-gray-800 text-gray-400">
              {tag}
            </span>
          ))}
        </div>
      )}
      <button
        onClick={() => onChat(character.id)}
        className="w-full py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-sm text-white font-medium transition-colors"
      >
        Chat
      </button>
    </div>
  );
}

function CreateCharacterModal({ onClose, onCreate }: { onClose: () => void; onCreate: (c: Character) => void }) {
  const [form, setForm] = useState<CreateCharacterBody>({
    name: "",
    description: "",
    personality: "",
    system_prompt: "",
    tags: [],
    is_public: false,
  });
  const [tagInput, setTagInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function set<K extends keyof CreateCharacterBody>(k: K) {
    return (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
      setForm((f) => ({ ...f, [k]: e.target.value }));
  }

  function addTag() {
    const tag = tagInput.trim();
    if (tag && !form.tags?.includes(tag)) {
      setForm((f) => ({ ...f, tags: [...(f.tags ?? []), tag] }));
    }
    setTagInput("");
  }

  function removeTag(tag: string) {
    setForm((f) => ({ ...f, tags: f.tags?.filter((t) => t !== tag) }));
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const char = await charactersApi.create({
        ...form,
        description: form.description || undefined,
        personality: form.personality || undefined,
        system_prompt: form.system_prompt || undefined,
      });
      onCreate(char);
      onClose();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to create character");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="bg-gray-900 border border-gray-800 rounded-xl w-full max-w-lg max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between p-4 border-b border-gray-800">
          <h2 className="font-semibold text-gray-100">New Character</h2>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-300 text-xl leading-none">×</button>
        </div>
        <form onSubmit={handleSubmit} className="p-4 space-y-4">
          <div>
            <label className="block text-sm text-gray-400 mb-1">Name *</label>
            <input value={form.name} onChange={set("name")} required maxLength={100}
              className="w-full px-3 py-2 rounded-lg bg-gray-800 border border-gray-700 text-gray-100 focus:outline-none focus:border-indigo-500 text-sm" />
          </div>
          <div>
            <label className="block text-sm text-gray-400 mb-1">Description</label>
            <input value={form.description ?? ""} onChange={set("description")} maxLength={500}
              className="w-full px-3 py-2 rounded-lg bg-gray-800 border border-gray-700 text-gray-100 focus:outline-none focus:border-indigo-500 text-sm" />
          </div>
          <div>
            <label className="block text-sm text-gray-400 mb-1">Personality</label>
            <textarea value={form.personality ?? ""} onChange={set("personality")} rows={3}
              className="w-full px-3 py-2 rounded-lg bg-gray-800 border border-gray-700 text-gray-100 focus:outline-none focus:border-indigo-500 text-sm resize-none" />
          </div>
          <div>
            <label className="block text-sm text-gray-400 mb-1">System prompt</label>
            <textarea value={form.system_prompt ?? ""} onChange={set("system_prompt")} rows={4}
              placeholder="You are {{name}}, a mysterious tavern keeper who..."
              className="w-full px-3 py-2 rounded-lg bg-gray-800 border border-gray-700 text-gray-100 focus:outline-none focus:border-indigo-500 text-sm resize-none font-mono" />
          </div>
          <div>
            <label className="block text-sm text-gray-400 mb-1">Tags</label>
            <div className="flex gap-2">
              <input value={tagInput} onChange={(e) => setTagInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && (e.preventDefault(), addTag())}
                placeholder="fantasy, romance…"
                className="flex-1 px-3 py-2 rounded-lg bg-gray-800 border border-gray-700 text-gray-100 focus:outline-none focus:border-indigo-500 text-sm" />
              <button type="button" onClick={addTag} className="px-3 py-2 rounded-lg bg-gray-700 text-sm text-gray-300 hover:bg-gray-600">Add</button>
            </div>
            {(form.tags?.length ?? 0) > 0 && (
              <div className="flex flex-wrap gap-1 mt-2">
                {form.tags?.map((tag) => (
                  <span key={tag} className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-gray-800 text-gray-300">
                    {tag}
                    <button type="button" onClick={() => removeTag(tag)} className="text-gray-500 hover:text-gray-200">×</button>
                  </span>
                ))}
              </div>
            )}
          </div>
          {error && <p className="text-sm text-red-400">{error}</p>}
          <div className="flex gap-3 pt-2">
            <button type="button" onClick={onClose} className="flex-1 py-2 rounded-lg border border-gray-700 text-sm text-gray-400 hover:text-gray-200 transition-colors">Cancel</button>
            <button type="submit" disabled={loading}
              className="flex-1 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-sm text-white font-medium transition-colors disabled:opacity-50">
              {loading ? "Creating…" : "Create"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export function CharactersPage() {
  const [characters, setCharacters] = useState<Character[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const navigate = useNavigate();

  useEffect(() => {
    charactersApi.list().then(setCharacters).finally(() => setLoading(false));
  }, []);

  async function handleChat(characterId: string) {
    const chat = await chatsApi.create(characterId);
    navigate(`/chat/${chat.id}`);
  }

  return (
    <div className="h-full overflow-y-auto p-6">
      <div className="max-w-4xl mx-auto">
        <div className="flex items-center justify-between mb-6">
          <h1 className="text-xl font-semibold">Characters</h1>
          <button
            onClick={() => setShowCreate(true)}
            className="px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-sm text-white font-medium transition-colors"
          >
            + New Character
          </button>
        </div>

        {loading ? (
          <div className="text-center text-gray-500 py-20">Loading…</div>
        ) : characters.length === 0 ? (
          <div className="text-center py-20">
            <p className="text-gray-500 mb-4">No characters yet.</p>
            <button onClick={() => setShowCreate(true)} className="text-indigo-400 hover:text-indigo-300 text-sm">
              Create your first character →
            </button>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {characters.map((c) => (
              <CharacterCard key={c.id} character={c} onChat={handleChat} />
            ))}
          </div>
        )}
      </div>

      {showCreate && (
        <CreateCharacterModal
          onClose={() => setShowCreate(false)}
          onCreate={(c) => setCharacters((prev) => [c, ...prev])}
        />
      )}
    </div>
  );
}
