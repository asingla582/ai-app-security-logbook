import { describe, expect, it, vi } from "vitest";

import { approveAction } from "./actions";

const action = {
  id: "a1", tool_name: "create_note", args: { title: "T", body: "B" },
  args_sha256: "abc123", flags: { urls: [], doc_spans: [] }, expires_at: "2026-10-07T12:00:00Z",
};

describe("approveAction", () => {
  it("posts only the displayed hash, never args", async () => {
    const spy = vi.spyOn(global, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ status: "executed", note_id: "n1" }), { status: 200 }),
    );
    expect(await approveAction(action, "tok")).toBe("executed");
    expect(spy.mock.calls[0][0]).toContain("/actions/a1/approve");
    expect(JSON.parse(spy.mock.calls[0][1]!.body as string)).toEqual({ args_sha256: "abc123" });
    spy.mockRestore();
  });

  it("maps 410 to expired and 409 to error", async () => {
    const spy = vi.spyOn(global, "fetch").mockResolvedValueOnce(new Response("{}", { status: 410 }))
      .mockResolvedValueOnce(new Response("{}", { status: 409 }));
    expect(await approveAction(action, "tok")).toBe("expired");
    expect(await approveAction(action, "tok")).toBe("error");
    spy.mockRestore();
  });
});
