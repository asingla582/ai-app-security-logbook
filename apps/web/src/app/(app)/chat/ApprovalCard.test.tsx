import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ApprovalCard, PREVIEW_CHARS, type PendingAction } from "./ApprovalCard";

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

  it("v1 preview is truncated", () => {
    expect(render(base)).not.toContain("x".repeat(PREVIEW_CHARS + 1));
  });
});
