import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export function Markdown({
  content,
  onCitation,
}: {
  content: string;
  onCitation?: (id: string) => void;
}) {
  const linked = content.replace(/\[(S\d+)\](?!\()/g, "[$1](#citation-$1)");
  return (
    <div className="markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        components={{
          a: ({ href, children }) =>
            href?.startsWith("#citation-") ? (
              <button
                className="citation"
                onClick={() => onCitation?.(href.slice(10))}
                aria-label={`Inspect source ${children}`}
              >
                {children}
              </button>
            ) : (
              <span>{children}</span>
            ),
          img: () => null,
        }}
      >
        {linked}
      </ReactMarkdown>
    </div>
  );
}
