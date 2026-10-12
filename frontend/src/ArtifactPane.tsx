import { useMemo, useState } from "react";
import DOMPurify from "dompurify";
import {
  Check,
  Copy,
  Download,
  Maximize2,
  Minimize2,
  X,
  BookOpen,
} from "lucide-react";
import { Markdown } from "./Markdown";
import type { Artifact, Evidence } from "./types";
import { usePaneFocus } from "./usePaneFocus";

const csp =
  "default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; img-src 'none'; font-src 'none'; connect-src 'none'; form-action 'none'; base-uri 'none'";
export function safePreview(content: string): string {
  const clean = DOMPurify.sanitize(content, {
    WHOLE_DOCUMENT: true,
    ADD_TAGS: ["style"],
    FORBID_TAGS: [
      "script",
      "iframe",
      "object",
      "embed",
      "form",
      "input",
      "button",
      "textarea",
      "select",
      "svg",
      "math",
      "link",
      "meta",
      "base",
      "img",
      "video",
      "audio",
    ],
    FORBID_ATTR: ["src", "srcset", "action", "formaction", "target"],
  });
  return clean.replace(
    /<head>/i,
    `<head><meta http-equiv="Content-Security-Policy" content="${csp}">`,
  );
}

export function ArtifactPane({
  artifact,
  close,
  showSources,
}: {
  artifact: Artifact;
  close: () => void;
  showSources: (sources: Evidence[]) => void;
}) {
  const pane = usePaneFocus(close);
  const [tab, setTab] = useState<"preview" | "source">("preview");
  const [expanded, setExpanded] = useState(false),
    [copied, setCopied] = useState(false),
    [error, setError] = useState("");
  const preview = useMemo(
    () => (artifact.type === "html" ? safePreview(artifact.content) : ""),
    [artifact.content, artifact.type],
  );
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(artifact.content);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      setError("Clipboard unavailable. Use Download to save this document.");
    }
  };
  const download = () => {
    const url = URL.createObjectURL(
      new Blob([artifact.content], {
        type: artifact.type === "html" ? "text/html" : "text/markdown",
      }),
    );
    const a = document.createElement("a");
    a.href = url;
    a.download = `${artifact.title
      .replace(/[^a-z0-9]+/gi, "-")
      .toLowerCase()
      .slice(0, 80)}.${artifact.type === "html" ? "html" : "md"}`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  return (
    <aside
      ref={pane}
      className={`artifact-pane ${expanded ? "expanded" : ""}`}
      aria-label="Artifact viewer"
    >
      <div className="pane-heading">
        <div>
          <span className="eyebrow">
            YOUR ARTIFACT · {artifact.type === "html" ? "HTML" : "MARKDOWN"}
          </span>
          <h2>{artifact.title}</h2>
        </div>
        <button
          className="icon-button"
          onClick={close}
          aria-label="Close artifact"
        >
          <X size={18} />
        </button>
      </div>
      <div className="artifact-toolbar">
        <div
          className="segmented"
          role="tablist"
          aria-label="Artifact view"
          onKeyDown={(event) => {
            if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key))
              return;
            event.preventDefault();
            const next =
              event.key === "Home"
                ? "preview"
                : event.key === "End"
                  ? "source"
                  : tab === "preview"
                    ? "source"
                    : "preview";
            setTab(next);
            event.currentTarget
              .querySelectorAll<HTMLButtonElement>("button")
              [next === "preview" ? 0 : 1]?.focus();
          }}
        >
          <button
            role="tab"
            id="artifact-preview-tab"
            aria-controls="artifact-panel"
            tabIndex={tab === "preview" ? 0 : -1}
            aria-selected={tab === "preview"}
            onClick={() => setTab("preview")}
          >
            Preview
          </button>
          <button
            role="tab"
            id="artifact-source-tab"
            aria-controls="artifact-panel"
            tabIndex={tab === "source" ? 0 : -1}
            aria-selected={tab === "source"}
            onClick={() => setTab("source")}
          >
            {artifact.type === "html" ? "HTML" : "Markdown"}
          </button>
        </div>
        <div className="toolbar-actions">
          <button
            className="icon-button"
            aria-label="Copy artifact"
            onClick={copy}
          >
            {copied ? <Check size={16} /> : <Copy size={16} />}
          </button>
          <button
            className="icon-button"
            aria-label="Download artifact"
            onClick={download}
          >
            <Download size={16} />
          </button>
          <button
            className="icon-button expand-button"
            aria-label={expanded ? "Collapse artifact" : "Expand artifact"}
            onClick={() => setExpanded(!expanded)}
          >
            {expanded ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
          </button>
        </div>
      </div>
      {error && (
        <p role="alert" className="error-inline">
          {error}
        </p>
      )}
      {artifact.type === "html" && (
        <p className="artifact-safety-note">
          Safe HTML preview · scripts and interactive controls are disabled.
        </p>
      )}
      <span className="sr-only" role="status">
        {copied ? "Artifact copied" : ""}
      </span>
      <div
        className={`artifact-body ${artifact.type === "html" && tab === "preview" ? "html-body" : ""}`}
        role="tabpanel"
        id="artifact-panel"
        aria-labelledby={`artifact-${tab}-tab`}
      >
        {tab === "source" ? (
          <pre>{artifact.content}</pre>
        ) : artifact.type === "markdown" ? (
          <Markdown
            content={artifact.content}
            onCitation={(id) =>
              showSources(
                artifact.evidence.filter((source) => source.citation === id),
              )
            }
          />
        ) : (
          <iframe
            title={artifact.title}
            sandbox=""
            referrerPolicy="no-referrer"
            srcDoc={preview}
          />
        )}
      </div>
      <div className="artifact-footer">
        <button onClick={() => showSources(artifact.evidence)}>
          <BookOpen size={14} /> {artifact.source_ids.length}{" "}
          {artifact.source_ids.length === 1 ? "source" : "sources"}
        </button>
        <span>
          {artifact.word_count.toLocaleString()} words ·{" "}
          {artifact.type === "html" ? "Isolated preview" : "Markdown"}
        </span>
      </div>
    </aside>
  );
}
