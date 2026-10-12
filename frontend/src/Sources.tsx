import { BookOpen, ExternalLink, X } from "lucide-react";
import type { Evidence } from "./types";
import { usePaneFocus } from "./usePaneFocus";

export function Sources({
  sources,
  close,
}: {
  sources: Evidence[];
  close: () => void;
}) {
  const pane = usePaneFocus(close);
  return (
    <aside ref={pane} className="sources-panel" aria-label="Source evidence">
      <div className="pane-heading">
        <div>
          <span className="eyebrow">THE EVIDENCE</span>
          <h2>From the conversation</h2>
        </div>
        <button
          className="icon-button"
          onClick={close}
          aria-label="Close sources"
        >
          <X size={18} />
        </button>
      </div>
      <p className="pane-intro">
        Original excerpts behind this response. Open an episode to explore the
        full conversation.
      </p>
      <div className="source-list">
        {!sources.length && (
          <p className="evidence-empty">
            No saved excerpt is attached to this reference. You can inspect the
            other sources in the answer.
          </p>
        )}
        {sources.map((source, index) => (
          <article
            className="source-card"
            key={source.chunk_id}
            id={`source-${source.citation}`}
            style={{ animationDelay: `${Math.min(index, 3) * 45}ms` }}
          >
            <div className="source-top">
              <span className="source-number">{source.citation}</span>
              <span>
                {source.publish_date
                  ? new Date(source.publish_date).toLocaleDateString(
                      undefined,
                      { month: "short", year: "numeric" },
                    )
                  : "Podcast transcript"}
              </span>
            </div>
            <h3>{source.guest}</h3>
            <p className="episode-title">{source.episode_title}</p>
            <p className="episode-title">
              Speakers:{" "}
              {source.speakers?.join(", ") || "Not identified in this passage"}
              {source.timestamp_start && (
                <>
                  {" "}
                  · {source.timestamp_start}
                  {source.timestamp_end &&
                  source.timestamp_end !== source.timestamp_start
                    ? `–${source.timestamp_end}`
                    : ""}
                </>
              )}
            </p>
            {source.excerpt.length > 650 ? (
              <details className="excerpt-details">
                <summary>Read transcript excerpt</summary>
                <blockquote>{source.excerpt}</blockquote>
              </details>
            ) : (
              <blockquote>{source.excerpt}</blockquote>
            )}
            {source.metadata_warning && (
              <p className="metadata-note">{source.metadata_warning}</p>
            )}
            <a
              href={
                source.source_url.startsWith("https://")
                  ? source.source_url
                  : undefined
              }
              target="_blank"
              rel="noopener noreferrer"
            >
              <BookOpen size={14} /> Open transcript <ExternalLink size={12} />
            </a>
            <details className="provenance">
              <summary>Source provenance</summary>
              <p>
                Revision {source.revision.slice(0, 12)}
                <br />
                {source.path}
                <br />
                Source ID {source.source_id}
                <br />
                Passage ID {source.chunk_id}
                {source.metadata_warning && source.original_metadata && (
                  <>
                    <br />
                    Archive title (unverified):{" "}
                    {String(source.original_metadata.title || "Unavailable")}
                    <br />
                    Archive date (unverified):{" "}
                    {String(
                      source.original_metadata.publish_date || "Unavailable",
                    )}
                  </>
                )}
              </p>
            </details>
          </article>
        ))}
      </div>
    </aside>
  );
}
