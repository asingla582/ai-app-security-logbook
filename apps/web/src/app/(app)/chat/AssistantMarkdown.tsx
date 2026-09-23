import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/**
 * Renderer for assistant messages, the second of two independent output
 * defenses. The API has already sanitized the text (images stripped, links
 * de-fanged unless their exact URL appeared in the retrieved sources), so
 * anything this component refuses to render is defense-in-depth, not policy:
 * images never render even if one slips through, raw HTML is never parsed
 * (react-markdown default), and javascript: hrefs are dropped by the default
 * URL transform. User messages stay plain text; only assistant output earns
 * markdown.
 */
export function AssistantMarkdown({ content }: { content: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        img: () => null,
        a: ({ href, children }) => (
          <a
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            className="underline underline-offset-2"
          >
            {children}
          </a>
        ),
      }}
    >
      {content}
    </ReactMarkdown>
  );
}
