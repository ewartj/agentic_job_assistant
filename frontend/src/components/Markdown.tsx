import ReactMarkdown from "react-markdown";

/**
 * Markdown from the API (assessments and plans). react-markdown never renders raw HTML,
 * so text that came from a job advert can't inject anything into the page.
 */
export function Markdown({ text }: { text: string }) {
  return (
    <div className="markdown">
      <ReactMarkdown
        components={{
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noopener noreferrer">
              {children}
            </a>
          ),
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
