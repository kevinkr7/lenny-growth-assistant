import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ArtifactPane, safePreview } from "./ArtifactPane";
import { Sources } from "./Sources";
import { Markdown } from "./Markdown";
import type { Artifact, Evidence } from "./types";
const source: Evidence = {
  citation: "S1",
  source_id: "source",
  chunk_id: "chunk",
  guest: "Test guest",
  episode_title: "A product conversation",
  excerpt: "A verifiable excerpt.",
  source_url: "https://example.org/episode",
  publish_date: "2025-01-01",
  revision: "abc123",
  path: "episodes/test/transcript.md",
  repository: "https://example.org",
};
const artifact: Artifact = {
  id: "one",
  title: "Product brief",
  type: "markdown",
  content: "# Product brief\n\nA useful idea [S1]",
  word_count: 6,
  evidence: [source],
  source_ids: ["source"],
  created_at: "",
};
describe("trust and artifact UI", () => {
  it("opens evidence through inline citations without rendering raw HTML", () => {
    const inspect = vi.fn();
    render(
      <Markdown
        content={"A fact [S1]\n\n<script>alert(1)</script>"}
        onCitation={inspect}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Inspect source S1" }));
    expect(inspect).toHaveBeenCalledWith("S1");
    expect(document.querySelector("script")).toBeNull();
  });
  it("shows original source text and provenance", () => {
    render(<Sources sources={[source]} close={() => {}} />);
    expect(screen.getByText("A verifiable excerpt.")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /Open transcript/ }),
    ).toHaveAttribute("rel", "noopener noreferrer");
  });
  it("shows conflicting archive metadata without guessing a replacement", () => {
    render(
      <Sources
        sources={[
          {
            ...source,
            speakers: ["Actual Speaker"],
            timestamp_start: "14:17",
            metadata_warning: "Archive title conflicts with transcript.",
            original_metadata: {
              title: "Original conflicting title",
              publish_date: "2024-01-01",
            },
          },
        ]}
        close={() => {}}
      />,
    );
    expect(
      screen.getByText("Archive title conflicts with transcript."),
    ).toBeInTheDocument();
    expect(screen.getByText(/Actual Speaker/)).toBeInTheDocument();
    expect(
      screen.getByText(
        /Archive title \(unverified\): Original conflicting title/,
      ),
    ).toBeInTheDocument();
  });
  it("switches a saved artifact from preview to source", () => {
    render(
      <ArtifactPane
        artifact={artifact}
        close={() => {}}
        showSources={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("tab", { name: "Markdown" }));
    expect(screen.getByRole("tabpanel").querySelector("pre")).toHaveTextContent(
      "# Product brief",
    );
  });
  it("renders an original checklist artifact with no podcast citations", () => {
    const content = "# Competitor Research Checklist\n\n- [ ] Identify the problem.\n- [ ] Test an idea.";
    render(
      <ArtifactPane
        artifact={{ ...artifact, title: "Competitor Research Checklist", content, evidence: [], source_ids: [] }}
        close={() => {}}
        showSources={() => {}}
      />,
    );
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Competitor Research Checklist");
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    expect(screen.queryByRole("button", { name: /Inspect source/ })).toBeNull();
    expect(screen.getByRole("button", { name: "Download artifact" })).toBeEnabled();
    fireEvent.click(screen.getByRole("tab", { name: "Markdown" }));
    expect(screen.getByRole("tabpanel").querySelector("pre")?.textContent).toBe(content);
  });
  it("isolates HTML and blocks injected script/forms", () => {
    const content =
      '<html><head></head><body><script>parent.alert(1)</script><form><input/></form><p onclick="alert(1)">Content</p></body></html>';
    const result = safePreview(content);
    expect(result).not.toContain("<script");
    expect(result).not.toContain("<form");
    expect(result).not.toContain("onclick");
    expect(result).toContain("script-src 'none'");
    render(
      <ArtifactPane
        artifact={{ ...artifact, type: "html", content }}
        close={() => {}}
        showSources={() => {}}
      />,
    );
    expect(screen.getByTitle("Product brief")).toHaveAttribute("sandbox", "");
  });
});
