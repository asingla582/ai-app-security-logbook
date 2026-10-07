import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ApprovalCard, type PendingAction } from "./ApprovalCard";

const base: PendingAction = {
  id: "a1", tool_name: "create_note",
  args: { title: "VPN steps", body: "x".repeat(200) },
  args_sha256: "h", flags: { urls: [], doc_spans: [] }, expires_at: "2026-10-07T12:00:00Z",
};
const noop = () => {};
const render = (a: PendingAction, status: "pending" | "executed" = "pending") =>
  renderToStaticMarkup(<ApprovalCard action={a} status={status} onApprove={noop} onDeny={noop} />);

describe("ApprovalCard", () => {
  it("shows the title and approve/deny while pending", () => {
    const html = render(base);
    expect(html).toContain("VPN steps");
    expect(html).toContain("Approve");
    expect(html).toContain("Deny");
  });

  it("shows a status line instead of buttons once decided", () => {
    const html = render(base, "executed");
    expect(html).not.toContain(">Approve<");
    expect(html).toContain("Note saved");
  });
});

const poisoned: PendingAction = {
  ...base,
  args: {
    title: "VPN setup steps",
    body: "1. Open GlobalConnect.\n".repeat(6) +
      "4. Re-authenticate once at https://sso-acme-verify.example/login before the VPN routes traffic.",
  },
  flags: {
    urls: [{ url: "https://sso-acme-verify.example/login", host: "sso-acme-verify.example", source: "onboarding.md" }],
    doc_spans: [{ source: "onboarding.md", excerpt: "Re-authenticate once at https://sso-acme-verify.example/login" }],
  },
};

describe("ApprovalCard v2", () => {
  it("shows the full body, untruncated", () => {
    expect(render(poisoned)).toContain("before the VPN routes traffic.");
  });

  it("lists every link with its host and source, never as an anchor", () => {
    const html = render(poisoned);
    expect(html).toContain("Links in this note");
    expect(html).toContain("sso-acme-verify.example");
    expect(html).toContain("from onboarding.md");
    expect(html).not.toMatch(/<a[\s>]/);
  });

  it("labels a link the assistant wrote itself", () => {
    const html = render({ ...poisoned, flags: { urls: [{ url: "https://x.example/", host: "x.example", source: "model" }], doc_spans: [] } });
    expect(html).toContain("written by the assistant");
  });

  it("shows text copied from a document with its source", () => {
    expect(render(poisoned)).toContain("Copied from onboarding.md");
  });

  it("renders markup in the body as inert text", () => {
    const html = render({ ...base, args: { title: "<b>t</b>", body: '<img src=x onerror=alert(1)> [click](https://e.example)' } });
    expect(html).not.toMatch(/<img[\s>]/);
    expect(html).not.toMatch(/<a[\s>]/);
    expect(html).toContain("&lt;img");
  });

  it("shows when the request expires", () => {
    expect(render(base)).toMatch(/Expires/);
  });
});
