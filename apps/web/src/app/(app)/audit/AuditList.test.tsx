import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { AuditList, type Anomaly, type AuditEvent } from "./AuditList";

const event: AuditEvent = {
  ts: "2026-11-04T10:00:00+00:00",
  kind: "approval",
  correlation_id: "cid-1",
  user_id: "u1",
  action: "create_note",
  outcome: "executed",
  subject_id: "a1",
  detail: { flagged_hosts: ["evil.example"] },
};

const anomaly: Anomaly = {
  rule: "sanitizer_stripped_url",
  severity: "medium",
  ts: event.ts,
  correlation_id: "cid-2",
  user_id: "u1",
  subject_id: "m1",
  detail: { images: ["evil.example"] },
};

function html(events: AuditEvent[], anomalies: Anomaly[]) {
  return renderToStaticMarkup(
    <AuditList events={events} anomalies={anomalies} filter="" onFilter={() => {}} />,
  );
}

describe("AuditList", () => {
  it("renders a hostile value as text, never as a link", () => {
    const hostile = { ...event, detail: { flagged_hosts: ["<a href='https://x.example'>x</a>"] } };
    const out = html([hostile], [{ ...anomaly, detail: hostile.detail }]);
    expect(out).not.toContain("<a ");
    expect(out).toContain("x.example");
  });

  it("shows findings, the verdict and the request id", () => {
    const out = html([event], [anomaly]);
    expect(out).toContain("sanitizer_stripped_url");
    expect(out).toContain("executed");
    expect(out).toContain("cid-1");
  });

  it("says so when there is nothing to show", () => {
    const out = html([], []);
    expect(out).toContain("No audit records.");
    expect(out).not.toContain("Findings");
  });
});
