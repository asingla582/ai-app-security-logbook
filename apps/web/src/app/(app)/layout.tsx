"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { supabase } from "@/lib/supabase";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [email, setEmail] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      if (!data.session) {
        router.replace("/login");
        return;
      }
      setEmail(data.session.user.email ?? null);
      setReady(true);
    });
  }, [router]);

  async function signOut() {
    await supabase.auth.signOut();
    router.replace("/login");
  }

  if (!ready) return null;

  return (
    <div className="min-h-screen bg-neutral-50">
      <header className="border-b border-neutral-200 bg-white">
        <div className="mx-auto flex max-w-3xl items-center justify-between px-4 py-3">
          <div className="flex items-center gap-4">
            <span className="text-sm font-semibold text-neutral-900">Security Logbook</span>
            <nav className="flex gap-3 text-sm text-neutral-500">
              <Link href="/" className="hover:text-neutral-900">
                Organizations
              </Link>
              <Link href="/chat" className="hover:text-neutral-900">
                Chat
              </Link>
            </nav>
          </div>
          <div className="flex items-center gap-3 text-sm text-neutral-500">
            <span>{email}</span>
            <button onClick={signOut} className="hover:text-neutral-900">
              Sign out
            </button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-3xl px-4 py-8">{children}</main>
    </div>
  );
}
