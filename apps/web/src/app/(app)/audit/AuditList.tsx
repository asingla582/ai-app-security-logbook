export type AuditEvent = {
  ts: string;
  kind: string;
  correlation_id: string;
  user_id: string;
  action: string;
  outcome: string;
  subject_id: string | null;
  detail: Record<string, unknown>;
};

export type Anomaly = {
  rule: string;
  severity: string;
  ts: string;
  correlation_id: string;
  user_id: string;
  subject_id: string | null;
  detail: Record<string, unknown>;
};

type Props = {
  events: AuditEvent[];
  anomalies: Anomaly[];
  filter: string;
  onFilter: (correlationId: string) => void;
};

// Everything here renders as plain text: audit values can hold hostnames an attacker
// chose, so nothing becomes a link or markup.
export function AuditList({ events, anomalies, filter, onFilter }: Props) {
  return (
    <div className="space-y-4">
      {anomalies.length > 0 && (
        <ul
          aria-label="Findings"
          className="space-y-1 rounded-xl border border-amber-300 bg-amber-50 p-4 text-sm text-amber-950"
        >
          {anomalies.map((a, i) => (
            <li key={`${a.rule}-${a.subject_id ?? a.ts}-${i}`} className="break-words">
              <strong>{a.severity}</strong>: {a.rule}, {JSON.stringify(a.detail)}
              <button
                className="ml-2 underline"
                onClick={() => onFilter(a.correlation_id)}
              >
                show
              </button>
            </li>
          ))}
        </ul>
      )}

      {filter && (
        <p className="text-sm text-neutral-600">
          Request <code className="break-all">{filter}</code>{" "}
          <button className="underline" onClick={() => onFilter("")}>
            clear
          </button>
        </p>
      )}

      {events.length === 0 ? (
        <p className="text-sm text-neutral-500">No audit records.</p>
      ) : (
        <ul className="divide-y divide-neutral-200 rounded-xl border border-neutral-200 bg-white">
          {events.map((e, i) => (
            <li key={`${e.kind}-${e.subject_id ?? i}-${e.ts}`} className="space-y-1 p-3 text-sm">
              <div className="flex flex-wrap justify-between gap-2 text-neutral-900">
                <span>
                  {e.kind}: {e.action} <strong>{e.outcome}</strong>
                </span>
                <time className="text-neutral-500" dateTime={e.ts}>
                  {new Date(e.ts).toLocaleString()}
                </time>
              </div>
              <button
                className="break-all text-left text-xs text-neutral-500 underline"
                onClick={() => onFilter(e.correlation_id)}
              >
                {e.correlation_id}
              </button>
              <p className="break-words text-xs text-neutral-500">{JSON.stringify(e.detail)}</p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
