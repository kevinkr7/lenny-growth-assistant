import { useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowUp,
  ArrowUpRight,
  BookOpen,
  Check,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  FileText,
  Layers3,
  Menu,
  MessageSquare,
  MoreHorizontal,
  PanelLeftClose,
  Plus,
  Radio,
  RefreshCw,
  Sparkles,
  Square,
  Trash2,
  X,
} from "lucide-react";
import { api, streamChat } from "./api";
import { Markdown } from "./Markdown";
import { Sources } from "./Sources";
import { ArtifactPane } from "./ArtifactPane";
import { Logo, StartupScreen } from "./Brand";
import type {
  Artifact,
  Evidence,
  Message,
  Mode,
  Provider,
  ProviderId,
  Readiness,
  Session,
  SessionDetail,
} from "./types";

const suggestions = [
  {
    label: "Find product–market fit",
    text: "How should a startup approach product-market fit?",
    icon: "01",
  },
  {
    label: "Make your next launch count",
    text: "What are the strongest lessons on product launches?",
    icon: "02",
  },
  {
    label: "Decide what to build next",
    text: "How should I prioritize features?",
    icon: "03",
  },
  {
    label: "Turn insight into an essay",
    text: "Write a Ship 30 essay about finding product-market fit.",
    icon: "04",
  },
];

const ACTIVE_SESSION_STORAGE_KEY = "lenny.active-session";

export default function App() {
  const [initialized, setInitialized] = useState(false);
  const [checkingProvider, setCheckingProvider] = useState(false);
  const [sessions, setSessions] = useState<Session[]>([]),
    [active, setActive] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]),
    [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [artifact, setArtifact] = useState<Artifact | null>(null),
    [sources, setSources] = useState<Evidence[] | null>(null);
  const [providers, setProviders] = useState<Provider[]>([]),
    [provider, setProvider] = useState<ProviderId>("ollama");
  const [providerMenu, setProviderMenu] = useState(false),
    [sidebar, setSidebar] = useState(false);
  const [readiness, setReadiness] = useState<Readiness | null>(null),
    [libraryOpen, setLibraryOpen] = useState(false);
  const [input, setInput] = useState(""),
    [busy, setBusy] = useState(false),
    [loading, setLoading] = useState(false);
  const [status, setStatus] = useState(""),
    [error, setError] = useState(""),
    [lastRequest, setLastRequest] = useState<{
      text: string;
      mode: Mode;
    } | null>(null);
  const [deleteId, setDeleteId] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null),
    bottom = useRef<HTMLDivElement>(null),
    composer = useRef<HTMLTextAreaElement>(null);
  const sticky = useRef(true),
    scrollArea = useRef<HTMLDivElement>(null),
    openSequence = useRef(0);
  const providerInitialized = useRef(false);
  const selectedProvider = providers.find((p) => p.id === provider);

  const refreshSessions = useCallback(
    async (signal?: AbortSignal) =>
      setSessions(await api<Session[]>("/api/sessions", { signal })),
    [],
  );
  const refreshStatus = useCallback(async (signal?: AbortSignal) => {
    setCheckingProvider(true);
    try {
      const res = await fetch("/readyz", {
          signal: signal ?? AbortSignal.timeout(10000),
        }),
        state: Readiness = await res.json();
      setReadiness(state);
      setProviders(state.providers);
      if (!providerInitialized.current) {
        setProvider(state.default_provider || "ollama");
        providerInitialized.current = true;
      }
    } catch {
      setError(
        "Cannot reach the assistant. Start the backend and retry connection.",
      );
    } finally {
      setCheckingProvider(false);
    }
  }, []);
  const reload = useCallback(async () => {
    setError("");
    const signal = AbortSignal.timeout(10000);
    const results = await Promise.allSettled([
      refreshSessions(signal),
      refreshStatus(signal),
    ]);
    for (const r of results)
      if (r.status === "rejected") setError(r.reason.message);
  }, [refreshSessions, refreshStatus]);
  useEffect(() => {
    let mounted = true;
    void reload().finally(() => {
      if (mounted) setInitialized(true);
    });
    return () => {
      mounted = false;
    };
  }, [reload]);
  useEffect(() => {
    return () => abort.current?.abort();
  }, []);
  useEffect(() => {
    if (sticky.current)
      bottom.current?.scrollIntoView({ behavior: "instant", block: "end" });
  }, [messages, status]);
  useEffect(() => {
    if (!libraryOpen && !deleteId && !sidebar && !providerMenu) return;
    const previous = document.activeElement as HTMLElement | null;
    const dialog = document.querySelector<HTMLElement>(
      libraryOpen || deleteId
        ? '[role="dialog"]'
        : sidebar
          ? ".sidebar"
          : ".provider-menu",
    );
    const focusables = () =>
      Array.from(
        dialog?.querySelectorAll<HTMLElement>(
          'button:not(:disabled), a[href], input, textarea, [tabindex="0"]',
        ) || [],
      );
    focusables()[0]?.focus();
    const trap = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setLibraryOpen(false);
        setDeleteId(null);
        setSidebar(false);
        setProviderMenu(false);
      }
      if (event.key !== "Tab") return;
      const items = focusables(),
        first = items[0],
        last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    };
    document.addEventListener("keydown", trap);
    return () => {
      document.removeEventListener("keydown", trap);
      previous?.focus();
    };
  }, [libraryOpen, deleteId, sidebar, providerMenu]);
  useEffect(() => {
    const query = window.matchMedia("(max-width: 700px)");
    const changed = () => {
      if (!query.matches) setSidebar(false);
    };
    query.addEventListener("change", changed);
    return () => query.removeEventListener("change", changed);
  }, []);

  const newChat = () => {
    if (busy) return;
    openSequence.current++;
    window.localStorage.removeItem(ACTIVE_SESSION_STORAGE_KEY);
    setActive(null);
    setMessages([]);
    setArtifacts([]);
    setArtifact(null);
    setSources(null);
    setInput("");
    setError("");
    setLastRequest(null);
    setSidebar(false);
    setLoading(false);
    composer.current?.focus();
  };
  const openSession = async (id: string) => {
    if (busy) return;
    const seq = ++openSequence.current;
    window.localStorage.setItem(ACTIVE_SESSION_STORAGE_KEY, id);
    setActive(id);
    setMessages([]);
    setArtifacts([]);
    setArtifact(null);
    setSources(null);
    setLoading(true);
    setError("");
    setLastRequest(null);
    setSidebar(false);
    try {
      const data = await api<SessionDetail>(`/api/sessions/${id}`);
      if (seq !== openSequence.current) return;
      setMessages(data.messages);
      setArtifacts(data.artifacts);
      sticky.current = true;
    } catch (e) {
      if (seq === openSequence.current) setError((e as Error).message);
    } finally {
      if (seq === openSequence.current) setLoading(false);
    }
  };
  useEffect(() => {
    if (active || loading || !sessions.length) return;
    const remembered = window.localStorage.getItem(ACTIVE_SESSION_STORAGE_KEY);
    if (!remembered) return;
    if (sessions.some((session) => session.id === remembered)) {
      void openSession(remembered);
    } else {
      window.localStorage.removeItem(ACTIVE_SESSION_STORAGE_KEY);
    }
  }, [active, loading, sessions]);
  const removeSession = async (id: string) => {
    try {
      await api(`/api/sessions/${id}`, { method: "DELETE" });
      if (active === id) newChat();
      setDeleteId(null);
      await refreshSessions();
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const send = async (text = input, mode: Mode = "auto") => {
    if (!text.trim() || busy || loading || !selectedProvider?.available) return;
    setBusy(true);
    setError("");
    setInput("");
    setLastRequest({ text, mode });
    sticky.current = true;
    setStatus("Connecting to your workspace");
    abort.current = new AbortController();
    const pendingId = crypto.randomUUID();
    let id = active;
    try {
      if (!id) {
        const session = await api<Session>("/api/sessions", {
          method: "POST",
          body: JSON.stringify({}),
        });
        id = session.id;
        window.localStorage.setItem(ACTIVE_SESSION_STORAGE_KEY, id);
        setActive(id);
        setSessions((s) => [session, ...s]);
      }
      setMessages((m) => [
        ...m,
        { id: crypto.randomUUID(), role: "user", content: text, metadata: {} },
        {
          id: pendingId,
          role: "assistant",
          content: "",
          metadata: { status: "streaming" },
        },
      ]);
      await streamChat(
        { session_id: id, message: text, provider, mode },
        abort.current.signal,
        (event, data) => {
          if (event === "accepted")
            setSessions((s) =>
              s.map((item) => (item.id === id ? data.session : item)),
            );
          if (event === "status") setStatus(data.message);
          if (event === "delta") {
            setStatus("Writing your answer");
            setMessages((m) =>
              m.map((item) =>
                item.id === pendingId
                  ? { ...item, content: item.content + data.text }
                  : item,
              ),
            );
          }
          if (event === "reset")
            setMessages((m) =>
              m.map((item) =>
                item.id === pendingId ? { ...item, content: "" } : item,
              ),
            );
          if (event === "artifact") {
            setArtifacts((a) => [...a, data]);
            setArtifact(data);
            setSources(null);
          }
          if (event === "done")
            setMessages((m) =>
              m.map((item) => (item.id === pendingId ? data.message : item)),
            );
          if (event === "error") {
            setError(data.message);
            setMessages((m) => m.filter((item) => item.id !== pendingId));
          }
        },
      );
    } catch (e) {
      const stopped = (e as Error).name === "AbortError";
      setError(
        stopped
          ? "Response stopped. Your conversation is saved."
          : (e as Error).message,
      );
      setMessages((m) => m.filter((item) => item.id !== pendingId));
    } finally {
      setBusy(false);
      setStatus("");
      abort.current = null;
      void refreshSessions().catch(() => {});
      if (!document.querySelector(".artifact-pane, .sources-panel"))
        composer.current?.focus();
    }
  };
  const showSources = (evidence: Evidence[]) => {
    setSources(evidence);
    setArtifact(null);
  };
  const openArtifact = (id: string) => {
    const found = artifacts.find((a) => a.id === id);
    if (found) {
      setArtifact(found);
      setSources(null);
    }
  };
  const dateGroup = (session: Session) =>
    new Date(session.updated_at).toDateString() === new Date().toDateString()
      ? "Today"
      : "Earlier";

  return (
    <>
      {!initialized && <StartupScreen />}
      <div
        className={`app-shell ${initialized ? "app-ready" : "app-initializing"}`}
        inert={!initialized}
        aria-hidden={!initialized || undefined}
      >
        {sidebar && (
          <button
            className="mobile-scrim"
            aria-label="Close navigation"
            onClick={() => setSidebar(false)}
          />
        )}
        <aside
          className={`sidebar ${sidebar ? "visible" : ""}`}
          role={sidebar ? "dialog" : undefined}
          aria-modal={sidebar || undefined}
          aria-label="Workspace navigation"
          inert={libraryOpen || !!deleteId}
        >
          <div className="sidebar-brand">
            <Logo />
            <div>
              <strong>
                lenny<span className="brand-period">.</span>
              </strong>
              <span>GROWTH ASSISTANT</span>
            </div>
            <button
              className="icon-button mobile-only"
              onClick={() => setSidebar(false)}
              aria-label="Close navigation"
            >
              <PanelLeftClose size={18} />
            </button>
          </div>
          <button className="new-chat" onClick={newChat} disabled={busy}>
            <Plus size={17} /> New conversation <span>↗</span>
          </button>
          <div className="nav-label">YOUR WORKSPACE</div>
          <button
            className="nav-item active"
            aria-current="page"
            onClick={() => {
              setSidebar(false);
              composer.current?.focus();
            }}
          >
            <MessageSquare size={16} /> Conversations{" "}
            <span>{sessions.length || "—"}</span>
          </button>
          <button className="nav-item" onClick={() => setLibraryOpen(true)}>
            <BookOpen size={16} /> Knowledge library <ArrowUpRight size={14} />
          </button>
          <div className="conversation-list" aria-label="Conversations">
            {sessions.length === 0 ? (
              <div className="no-conversations">
                <span>Room for your next idea.</span>
                <p>Your conversations will live here.</p>
              </div>
            ) : (
              ["Today", "Earlier"].map(
                (group) =>
                  sessions.some((s) => dateGroup(s) === group) && (
                    <div key={group}>
                      <div className="group-label">{group}</div>
                      {sessions
                        .filter((s) => dateGroup(s) === group)
                        .map((session) => (
                          <div
                            className={`session-row ${active === session.id ? "selected" : ""}`}
                            key={session.id}
                          >
                            <button
                              className="session-title"
                              data-session-id={session.id}
                              disabled={busy}
                              onClick={() => void openSession(session.id)}
                              title={session.title}
                            >
                              <MessageSquare size={14} />
                              <span>{session.title}</span>
                            </button>
                            <button
                              className="session-delete icon-button"
                              disabled={busy}
                              aria-label={`Delete ${session.title}`}
                              onClick={() => setDeleteId(session.id)}
                            >
                              <Trash2 size={13} />
                            </button>
                          </div>
                        ))}
                    </div>
                  ),
              )
            )}
          </div>
          <div className="sidebar-bottom">
            <div className="library-note">
              <span
                className={`connection-dot ${readiness?.checks.corpus ? "online" : ""}`}
              />
              <span>
                {readiness?.library.sources
                  ? `${readiness.library.sources} conversations, indexed`
                  : readiness
                    ? "Transcript library needs setup"
                    : "Connection unavailable"}
              </span>
            </div>
            <button
              className="workspace-profile"
              onClick={() => setLibraryOpen(true)}
            >
              <span className="profile-avatar">W</span>
              <span>
                <strong>Personal workspace</strong>
                <small>Built for better product thinking</small>
              </span>
              <MoreHorizontal size={16} />
            </button>
          </div>
        </aside>

        <div className="workspace" inert={sidebar || libraryOpen || !!deleteId}>
          <header className="workspace-header">
            <div className="breadcrumb">
              <button
                className="icon-button mobile-only"
                onClick={() => setSidebar(true)}
                aria-label="Open navigation"
              >
                <Menu size={19} />
              </button>
              <span>Workspace</span>
              <ChevronRight size={14} />
              <strong>
                {active
                  ? sessions.find((s) => s.id === active)?.title ||
                    "Conversation"
                  : "New conversation"}
              </strong>
            </div>
            <div className="header-actions">
              <div className="provider-control">
                <button
                  className="provider-button"
                  aria-expanded={providerMenu}
                  aria-controls="provider-options"
                  onClick={() => setProviderMenu(!providerMenu)}
                  disabled={busy}
                >
                  <span
                    className={`connection-dot ${(selectedProvider?.verified ?? (selectedProvider?.id === "ollama" && selectedProvider.available)) ? "online" : ""}`}
                  />
                  <span className="provider-label">
                    {provider === "ollama" ? "Local · Ollama" : "Cloud · Gemini"}
                    <small title={selectedProvider?.model} aria-live="polite">
                      {checkingProvider
                        ? "Checking connection…"
                        : selectedProvider?.model || "Not connected"}
                    </small>
                  </span>
                  <ChevronDown size={13} />
                </button>
                {providerMenu && (
                  <>
                    <button
                      className="menu-dismiss"
                      tabIndex={-1}
                      aria-label="Close model selector"
                      onClick={() => setProviderMenu(false)}
                    />
                    <div
                      className="provider-menu"
                      id="provider-options"
                      role="group"
                      aria-label="Choose your model"
                    >
                      <span className="eyebrow">CHOOSE YOUR MODEL</span>
                      {(["ollama", "gemini"] as ProviderId[]).map(
                        (id) => {
                          const item = providers.find((p) => p.id === id);
                          return (
                            <button
                              key={id}
                              disabled={!item?.available}
                              title={item?.message}
                              onClick={() => {
                                setProvider(id);
                                setProviderMenu(false);
                              }}
                            >
                              <span className="provider-option-icon">
                                {id === "ollama" ? (
                                  <Radio size={17} />
                                ) : (
                                  <Sparkles size={17} />
                                )}
                              </span>
                              <span>
                                <strong>
                                  {id === "ollama"
                                    ? "Ollama · Local"
                                    : "Gemini · Cloud"}
                                </strong>
                                <small>{item?.model || "Not connected"}</small>
                                <small
                                  className={
                                    !item?.available ? "unavailable" : ""
                                  }
                                >
                                  {item?.message ||
                                    "Start the backend to connect."}
                                </small>
                              </span>
                              {id === provider && <Check size={15} />}
                            </button>
                          );
                        },
                      )}
                      <button
                        className="retry-provider"
                        disabled={checkingProvider}
                        onClick={() => void refreshStatus()}
                      >
                        <RefreshCw
                          size={13}
                          className={checkingProvider ? "is-checking" : ""}
                        />{" "}
                        {checkingProvider
                          ? "Checking connection…"
                          : "Retry connection"}
                      </button>
                    </div>
                  </>
                )}
              </div>
              <button
                className="icon-button help-button"
                onClick={() => setLibraryOpen(true)}
                aria-label="Workspace status and help"
              >
                <CircleHelp size={18} />
              </button>
            </div>
          </header>

          <div className="workspace-content">
            <main
              className={`chat-column ${messages.length === 0 ? "is-empty" : ""}`}
            >
              <div
                className="chat-scroll"
                ref={scrollArea}
                onScroll={() => {
                  const el = scrollArea.current;
                  if (el)
                    sticky.current =
                      el.scrollHeight - el.scrollTop - el.clientHeight < 100;
                }}
              >
                {loading ? (
                  <div className="loading-state" role="status">
                    <span className="loading-dot" /> Opening your conversation…
                  </div>
                ) : messages.length === 0 ? (
                  <div className="empty-state">
                    <div className="intro-kicker">
                      <span className="tiny-line" /> GOOD QUESTIONS. GROUNDED
                      ANSWERS.
                    </div>
                    <h1>
                      The Lenny
                      <br />
                      Growth <span>Assistant.</span>
                    </h1>
                    <p className="intro-copy">
                      Ask product and growth questions grounded in Lenny's
                      Podcast.
                      <br className="desktop-break" /> Turn what you learn into
                      something worth shipping.
                    </p>
                    <div className="suggestion-grid">
                      {suggestions.map((item) => (
                        <button
                          key={item.icon}
                          disabled={
                            busy || loading || !selectedProvider?.available
                          }
                          onClick={() => void send(item.text)}
                        >
                          <span
                            className="suggestion-number"
                            aria-hidden="true"
                          >
                            {item.icon === "04" ? (
                              <FileText size={17} />
                            ) : item.icon === "03" ? (
                              <Layers3 size={17} />
                            ) : item.icon === "02" ? (
                              <Radio size={17} />
                            ) : (
                              <Sparkles size={17} />
                            )}
                          </span>
                          <span>{item.label}</span>
                          <ArrowUpRight size={16} />
                        </button>
                      ))}
                    </div>
                    <div className="trust-note">
                      <BookOpen size={14} />
                      <span>
                        Real conversations. Visible sources. Your perspective.
                      </span>
                    </div>
                  </div>
                ) : (
                  <div className="messages">
                    {messages.map((message) => (
                      <article
                        className={`message ${message.role}`}
                        key={message.id}
                      >
                        <div className="message-heading">
                          {message.role === "assistant" ? (
                            <Logo small />
                          ) : (
                            <span className="user-avatar">Y</span>
                          )}
                          <strong>
                            {message.role === "assistant" ? "Lenny" : "You"}
                          </strong>
                          {message.role === "assistant" && (
                            <span className="message-model">
                              {message.metadata.response_kind ===
                              "source_lookup"
                                ? "Source lookup"
                                : message.model ||
                                  (message.metadata.status === "streaming"
                                    ? selectedProvider?.model
                                    : "")}
                            </span>
                          )}
                        </div>
                        <div className="message-content">
                          {message.content ? (
                            <Markdown
                              content={message.content}
                              onCitation={(id) =>
                                showSources(
                                  (message.metadata.sources || []).filter(
                                    (source) => source.citation === id,
                                  ),
                                )
                              }
                            />
                          ) : (
                            <div
                              className="thinking"
                              aria-label="Lenny is composing"
                              role="status"
                            >
                              <span />
                              <span />
                              <span />
                            </div>
                          )}
                          {message.metadata.status === "streaming" &&
                            message.content && (
                              <span className="stream-caret" />
                            )}
                          {!!message.metadata.sources?.length && (
                            <button
                              className="grounding-strip"
                              onClick={() =>
                                showSources(message.metadata.sources!)
                              }
                            >
                              <BookOpen size={13} />
                              <span>
                                {message.metadata.abstained
                                  ? "Inspect"
                                  : "Grounded in"}{" "}
                                {
                                  new Set(
                                    message.metadata.sources.map(
                                      (s) => s.source_id,
                                    ),
                                  ).size
                                }{" "}
                                {new Set(
                                  message.metadata.sources.map(
                                    (s) => s.source_id,
                                  ),
                                ).size === 1
                                  ? "source"
                                  : "sources"}
                              </span>
                              <span className="mini-avatars">
                                {[
                                  ...new Set(
                                    message.metadata.sources.map(
                                      (s) => s.guest,
                                    ),
                                  ),
                                ]
                                  .slice(0, 3)
                                  .map((name) => (
                                    <i key={name}>
                                      {name
                                        .split(" ")
                                        .map((w) => w[0])
                                        .slice(0, 2)
                                        .join("")}
                                    </i>
                                  ))}
                              </span>
                              <ChevronRight size={13} />
                            </button>
                          )}
                          {(
                            message.metadata.artifact_ids ||
                            (message.metadata.artifact_id
                              ? [message.metadata.artifact_id]
                              : [])
                          ).map((artifactId) => (
                            <button
                              key={artifactId}
                              className="artifact-card"
                              onClick={() => openArtifact(artifactId)}
                            >
                              <span>
                                <FileText size={21} />
                              </span>
                              <div>
                                <strong>
                                  {artifacts.find((a) => a.id === artifactId)
                                    ?.title || "Open artifact"}
                                </strong>
                                <small>
                                  {artifacts.find((a) => a.id === artifactId)
                                    ?.type === "html"
                                    ? "HTML artifact"
                                    : "Markdown artifact"}{" "}
                                  · Open, copy or download
                                </small>
                              </div>
                              <ArrowUpRight size={16} />
                            </button>
                          ))}
                          {message.metadata.status === "failed" && (
                            <span className="failed-label">
                              Response could not be completed
                            </span>
                          )}
                        </div>
                      </article>
                    ))}
                    {!busy &&
                      messages.some(
                        (m) =>
                          m.role === "assistant" && m.metadata.sources?.length,
                      ) && (
                        <div className="followup-actions">
                          <span>MAKE IT USEFUL</span>
                          <button
                            onClick={() =>
                              void send(
                                "Turn this into a Ship 30 essay.",
                                "ship30",
                              )
                            }
                          >
                            <FileText size={13} /> Write an essay
                          </button>
                          <button
                            onClick={() =>
                              void send(
                                "Turn this into a landing page.",
                                "html",
                              )
                            }
                          >
                            <Layers3 size={13} /> Build a page
                          </button>
                        </div>
                      )}
                  </div>
                )}
                <div ref={bottom} />
              </div>
              <div className="composer-region">
                {!error &&
                  !busy &&
                  !checkingProvider &&
                  selectedProvider &&
                  !selectedProvider.available && (
                    <div className="error-banner" role="status">
                      <span>
                        {selectedProvider.message ||
                          "This provider is unavailable. Choose another model or retry the connection."}
                      </span>
                      <button onClick={() => void refreshStatus()}>
                        <RefreshCw size={13} /> Retry connection
                      </button>
                    </div>
                  )}
                {error && (
                  <div className="error-banner" role="alert">
                    <span>{error}</span>
                    <button
                      onClick={() =>
                        lastRequest
                          ? void send(lastRequest.text, lastRequest.mode)
                          : void reload()
                      }
                      disabled={busy}
                    >
                      <RefreshCw size={13} />{" "}
                      {lastRequest ? "Try again" : "Retry connection"}
                    </button>
                    <button
                      className="icon-button"
                      aria-label="Dismiss error"
                      onClick={() => setError("")}
                    >
                      <X size={14} />
                    </button>
                  </div>
                )}
                {busy && (
                  <div className="progress" role="status" aria-live="polite">
                    <span className="loading-dot" />
                    {status}
                  </div>
                )}
                <form
                  className="composer"
                  onSubmit={(e) => {
                    e.preventDefault();
                    void send();
                  }}
                >
                  <textarea
                    ref={composer}
                    aria-label="Ask Lenny a question"
                    placeholder={
                      messages.length
                        ? "Ask a follow-up, or turn this into something…"
                        : "What are you working through?"
                    }
                    value={input}
                    maxLength={4000}
                    disabled={loading}
                    rows={2}
                    onChange={(e) => setInput(e.target.value)}
                    onKeyDown={(e) => {
                      if (
                        e.key === "Enter" &&
                        !e.shiftKey &&
                        !e.nativeEvent.isComposing
                      ) {
                        e.preventDefault();
                        void send();
                      }
                    }}
                  />
                  <div className="composer-bottom">
                    <span>
                      <BookOpen size={13} /> Lenny's Podcast
                    </span>
                    <div>
                      <small>
                        {input.length > 3600
                          ? `${input.length}/4000`
                          : "↵ to send"}
                      </small>
                      {busy ? (
                        <button
                          type="button"
                          className="send-button"
                          aria-label="Stop response"
                          onClick={() => abort.current?.abort()}
                        >
                          <Square size={13} fill="currentColor" />
                        </button>
                      ) : (
                        <button
                          type="submit"
                          className="send-button"
                          aria-label="Send message"
                          disabled={
                            !input.trim() ||
                            loading ||
                            !selectedProvider?.available
                          }
                        >
                          <ArrowUp size={18} />
                        </button>
                      )}
                    </div>
                  </div>
                </form>
                <p className="composer-caption">
                  Grounded in the podcast. Made for your next step.
                  <span>Always explore the sources.</span>
                </p>
              </div>
            </main>
            {sources && (
              <Sources sources={sources} close={() => setSources(null)} />
            )}{" "}
            {artifact && (
              <ArtifactPane
                key={artifact.id}
                artifact={artifact}
                close={() => setArtifact(null)}
                showSources={showSources}
              />
            )}
          </div>
        </div>
        {libraryOpen && (
          <div className="modal-backdrop" onClick={() => setLibraryOpen(false)}>
            <section
              className="status-modal"
              role="dialog"
              aria-modal="true"
              aria-labelledby="library-title"
              onClick={(e) => e.stopPropagation()}
              onKeyDown={(e) => {
                if (e.key === "Escape") setLibraryOpen(false);
              }}
            >
              <button
                className="icon-button modal-close"
                autoFocus
                onClick={() => setLibraryOpen(false)}
                aria-label="Close workspace status"
              >
                <X size={18} />
              </button>
              <span className="eyebrow">CONNECTED KNOWLEDGE</span>
              <h2 id="library-title">A library you can trace back.</h2>
              <p>
                Knowledge answers use transcripts from Lenny's Podcast. Source
                cards show the original passage and episode.
              </p>
              <div className="library-stats">
                <div>
                  <strong>{readiness?.library.sources ?? "—"}</strong>
                  <span>episodes indexed</span>
                </div>
                <div>
                  <strong>
                    {readiness?.library.chunks.toLocaleString() ?? "—"}
                  </strong>
                  <span>searchable passages</span>
                </div>
              </div>
              <div className="readiness-checks">
                {Object.entries(readiness?.checks || { backend: false }).map(
                  ([key, ok]) => (
                    <div key={key}>
                      <span
                        className={`connection-dot ${ok ? "online" : ""}`}
                      />
                      <span>
                        {key === "vector"
                          ? "Semantic search"
                          : key === "corpus"
                            ? "Transcript library"
                            : key === "database"
                              ? "Conversation storage"
                              : "Backend connection"}
                      </span>
                      <small>{ok ? "Connected" : "Setup needed"}</small>
                    </div>
                  ),
                )}
              </div>
              {readiness?.guidance && (
                <p className="setup-note">
                  {readiness.guidance} See the README for setup commands.
                </p>
              )}
              <p className="privacy-note">
                <strong>
                  {provider === "ollama"
                    ? "Local inference selected."
                    : "Cloud inference selected."}
                </strong>{" "}
                {provider === "ollama"
                  ? "Chat inference stays on your machine. The first setup downloads the transcript archive and embedding/model files."
                  : "Your request, bounded chat context and retrieved passages are sent to Google Gemini. The app never switches providers automatically."}
              </p>
              <button
                className="secondary-button"
                onClick={() => void reload()}
              >
                <RefreshCw size={14} /> Retry connection
              </button>
            </section>
          </div>
        )}
        {deleteId && (
          <div className="modal-backdrop">
            <section
              role="dialog"
              aria-modal="true"
              aria-labelledby="delete-title"
              className="status-modal"
            >
              <h2 id="delete-title">Delete this conversation?</h2>
              <p>
                Its messages and artifacts will be removed from this workspace.
              </p>
              <div className="dialog-actions">
                <button
                  className="secondary-button"
                  autoFocus
                  onClick={() => setDeleteId(null)}
                >
                  Keep conversation
                </button>
                <button
                  className="danger-button"
                  onClick={() => void removeSession(deleteId)}
                >
                  Delete conversation
                </button>
              </div>
            </section>
          </div>
        )}
      </div>
    </>
  );
}
