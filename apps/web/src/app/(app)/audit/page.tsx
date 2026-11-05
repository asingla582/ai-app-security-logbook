"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { apiFetch } from "@/lib/api";
import { supabase } from "@/lib/supabase";

import { AuditList, type Anomaly, type AuditEvent } from "./AuditList";

async function token(): Promise<string> {
  const { data } = await supabase.auth.getSession();
  return data.session?.access_token ?? "";
}

export default function AuditPage() {
  const [orgId, setOrgId] = useState<string | null>(null);
  const [orgName, setOrgName] = useState("");
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [anomalies, setAnomalies] = useState<Anomaly[]>([]);
  const [filter, setFilter] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const latest = useRef(0);

  useEffect(() => {
    setOrgId(localStorage.getItem("orgId"));
    setOrgName(localStorage.getItem("orgName") ?? "");
  }, []);

  const load = useCallback(async (id: string, correlationId: string) => {
    // A slower, older response must not replace the filter the viewer last chose.
    const request = ++latest.current;
    const query = correlationId ? `?correlation_id=${encodeURIComponent(correlationId)}` : "";
    try {
      const res = await apiFetch(`/orgs/${id}/audit${query}`, await token());
      if (request !== latest.current) return;
      if (!res.ok) {
        setProblem(
          res.status === 404
            ? "Only an organization owner can read its audit."
            : "The audit could not be loaded.",
        );
        return;
      }
      const body = await res.json();
      if (request !== latest.current) return;
      setProblem(null);
      setEvents(body.events);
      setAnomalies(body.anomalies);
    } catch {
      if (request === latest.current) setProblem("The audit could not be loaded.");
    }
  }, []);

  useEffect(() => {
    if (orgId) load(orgId, filter);
  }, [orgId, filter, load]);

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
      <h1 className="text-xl font-semibold text-neutral-900">{orgName} audit timeline</h1>
      {problem ? (
        <p className="text-sm text-neutral-500">{problem}</p>
      ) : (
        <AuditList
          events={events}
          anomalies={anomalies}
          filter={filter}
          onFilter={setFilter}
        />
      )}
    </div>
  );
}
