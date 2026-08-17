"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { apiFetch } from "@/lib/api";
import { supabase } from "@/lib/supabase";

type Org = { id: string; name: string };

async function token(): Promise<string> {
  const { data } = await supabase.auth.getSession();
  return data.session?.access_token ?? "";
}

export default function OrgsPage() {
  const [orgs, setOrgs] = useState<Org[]>([]);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);

  async function load() {
    const res = await apiFetch("/orgs", await token());
    if (res.ok) setOrgs(await res.json());
  }

  useEffect(() => {
    load();
  }, []);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    await apiFetch("/orgs", await token(), {
      method: "POST",
      body: JSON.stringify({ name }),
    });
    setName("");
    setBusy(false);
    load();
  }

  function select(org: Org) {
    localStorage.setItem("orgId", org.id);
    localStorage.setItem("orgName", org.name);
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-neutral-900">Your organizations</h1>
        <p className="mt-1 text-sm text-neutral-500">
          You only see organizations you belong to. Isolation is enforced by the database.
        </p>
      </div>

      <form onSubmit={create} className="flex gap-2">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="New organization name"
          className="flex-1 rounded-lg border border-neutral-300 px-3 py-2 text-sm outline-none focus:border-neutral-900"
        />
        <button
          type="submit"
          disabled={busy}
          className="rounded-lg bg-neutral-900 px-4 py-2 text-sm font-medium text-white hover:bg-neutral-800 disabled:opacity-50"
        >
          Create
        </button>
      </form>

      <ul className="divide-y divide-neutral-200 overflow-hidden rounded-xl border border-neutral-200 bg-white">
        {orgs.length === 0 && (
          <li className="px-4 py-6 text-sm text-neutral-400">No organizations yet.</li>
        )}
        {orgs.map((org) => (
          <li key={org.id} className="flex items-center justify-between px-4 py-3">
            <span className="text-sm text-neutral-900">{org.name}</span>
            <Link
              href="/notes"
              onClick={() => select(org)}
              className="text-sm text-neutral-500 hover:text-neutral-900"
            >
              Open notes →
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
