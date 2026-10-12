export type ProviderId = "ollama" | "gemini";
export type Mode = "auto" | "answer" | "ship30" | "markdown" | "html" | "both";
export interface Provider {
  id: ProviderId;
  name: string;
  model: string;
  available: boolean;
  message: string;
  state?: string;
  verified?: boolean;
}
export interface Session {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}
export interface Evidence {
  metadata_warning?: string | null;
  original_metadata?: Record<string, unknown>;
  author?: string | null;
  speakers?: string[];
  timestamp_start?: string | null;
  timestamp_end?: string | null;
  citation: string;
  source_id: string;
  chunk_id: string;
  guest: string;
  episode_title: string;
  excerpt: string;
  source_url: string;
  publish_date: string | null;
  revision: string;
  path: string;
  repository: string;
}
export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  provider?: string;
  model?: string;
  created_at?: string;
  metadata: {
    sources?: Evidence[];
    artifact_id?: string;
    artifact_ids?: string[];
    response_kind?: string;
    grounding?: { verdict: string; method: string };
    status?: string;
    abstained?: boolean;
  };
}
export interface Artifact {
  id: string;
  type: "markdown" | "html";
  title: string;
  content: string;
  word_count: number;
  evidence: Evidence[];
  source_ids: string[];
  created_at: string;
}
export interface SessionDetail extends Session {
  messages: Message[];
  artifacts: Artifact[];
}
export interface Readiness {
  default_provider?: ProviderId;
  status: string;
  checks: Record<string, boolean>;
  library: { sources: number; chunks: number };
  providers: Provider[];
  guidance: string | null;
}
