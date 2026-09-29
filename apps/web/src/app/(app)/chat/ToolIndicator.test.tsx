import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ToolIndicator } from "./ToolIndicator";

const render = (toolUsed: { name: string; summary: string } | null) =>
  renderToStaticMarkup(<ToolIndicator toolUsed={toolUsed} />);

describe("ToolIndicator", () => {
  it("renders nothing when no tool was used", () => {
    expect(render(null)).toBe("");
  });

  it("labels a document search", () => {
    const html = render({ name: "search_documents", summary: "searched for x: 3 passage(s)" });
    expect(html).toContain("searched documents");
  });

  it("labels a note creation", () => {
    const html = render({ name: "create_note", summary: "created note 1 titled 'Standup'" });
    expect(html).toContain("created a note");
  });

  it("carries the summary as a title tooltip, not inline body", () => {
    const html = render({ name: "create_note", summary: "created note 1 titled 'Standup'" });
    expect(html).toContain('title="created note 1 titled &#x27;Standup&#x27;"');
  });

  it("falls back to the raw tool name for an unknown tool", () => {
    const html = render({ name: "future_tool", summary: "did something" });
    expect(html).toContain("future_tool");
  });
});
