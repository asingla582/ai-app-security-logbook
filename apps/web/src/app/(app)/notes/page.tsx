"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { apiFetch } from "@/lib/api";
import { supabase } from "@/lib/supabase";

type Note = { id: string; title: string; body: string };

async function token(): Promise<string> {
  const { data } = await supabase.auth.getSession();
  return data.session?.access_token ?? "";
}

export default function NotesPage() {
  const [orgId, setOrgId] = useState<string | null>(null);
  const [orgName, setOrgName] = useState<string>("");
  const [notes, setNotes] = useState<Note[]>([]);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");

  useEffect(() => {
    setOrgId(localStorage.getItem("orgId"));
    setOrgName(localStorage.getItem("orgName") ?? "");
  }, []);

  async function load(id: string) {
    const res = await apiFetch(`/orgs/${id}/notes`, await token());
    if (res.ok) setNotes(await res.json());
  }

  useEffect(() => {
    if (orgId) load(orgId);
  }, [orgId]);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    if (!orgId || !title.trim()) return;
    await apiFetch(`/orgs/${orgId}/notes`, await token(), {
      method: "POST",
      body: JSON.stringify({ title, body }),
    });
    setTitle("");
    setBody("");
    load(orgId);
  }

  if (!orgId) {
    return (
      <p className="text-sm text-neutral-500">
        No organization selected.{" "}
        <Link href="/" className="text-neutral-900 underline">
          Pick one
        </Link>
        .
      </p>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <Link href="/" className="text-sm text-neutral-500 hover:text-neutral-900">
          ← Organizations
        </Link>
        <h1 className="mt-2 text-xl font-semibold text-neutral-900">{orgName} notes</h1>
      </div>

      <form onSubmit={create} className="space-y-2 rounded-xl border border-neutral-200 bg-white p-4">
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Title"
          className="w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm outline-none focus:border-neutral-900"
        />
        <textarea
          value={body}
          onChange={(e) => setBody(e.target.value)}
          placeholder="Body"
          rows={3}
          className="w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm outline-none focus:border-neutral-900"
        />
        <button
          type="submit"
          className="rounded-lg bg-neutral-900 px-4 py-2 text-sm font-medium text-white hover:bg-neutral-800"
        >
          Add note
        </button>
      </form>

      <ul className="space-y-2">
        {notes.length === 0 && <li className="text-sm text-neutral-400">No notes yet.</li>}
        {notes.map((note) => (
          <li key={note.id} className="rounded-xl border border-neutral-200 bg-white p-4">
            <p className="text-sm font-medium text-neutral-900">{note.title}</p>
            {note.body && <p className="mt-1 text-sm text-neutral-500">{note.body}</p>}
          </li>
        ))}
      </ul>
    </div>
  );
}
