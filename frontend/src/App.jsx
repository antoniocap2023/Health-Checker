import { useState, useEffect } from "react";

// Relative path — we ask whoever served this page for "/api/chat", and a reverse
// proxy routes it to the backend. In production that proxy is nginx (see
// frontend/nginx.conf); in local `npm run dev` it's Vite's dev proxy (see
// vite.config.js). Either way the browser never needs the backend's address, so
// this one URL works unchanged on a laptop, in Docker, and on AWS.
const API_URL = "/api/chat";

// localStorage key where we remember the current conversation's id, so a refresh
// (or coming back later) resumes the same conversation instead of starting over.
const CONVERSATION_KEY = "health-checker-conversation-id";

// The model replies in Markdown, but we render plain text, so the raw markers
// would show literally. Strip the few it uses: **bold**, ## headings, and the
// > blockquote arrows. We run this on the whole message string at render time
// (not per stream chunk) so a marker split across chunks is still caught.
function stripMarkdown(text) {
  return text
    .replace(/\*/g, "") // remove * markers — both **bold** and *italic*
    .replace(/^\s*#{1,6}\s*/gm, "") // remove leading #/##/### headings
    .replace(/^\s*>\s?/gm, ""); // remove > blockquote arrows
}

// Turn the optional `filters` object on a search event into a short human label,
// e.g. "meta analysis, systematic review · 2020–2024". Returns null when no
// filters were applied (the common case), so the marker stays uncluttered.
function formatFilters(filters) {
  if (!filters) return null;
  const parts = [];
  if (filters.publication_types?.length) {
    parts.push(filters.publication_types.map((t) => t.replace(/-/g, " ")).join(", "));
  }
  const { min_year, max_year, last_n_years } = filters;
  if (last_n_years) parts.push(`last ${last_n_years} years`);
  else if (min_year && max_year) parts.push(`${min_year}–${max_year}`);
  else if (min_year) parts.push(`${min_year}+`);
  else if (max_year) parts.push(`up to ${max_year}`);
  return parts.length ? parts.join(" · ") : null;
}

export default function App() {
  // The whole conversation. Items are one of:
  //   { role: "user" | "assistant", content }  — a chat bubble
  //   { role: "search", query, max_results, filters } — a "searched PubMed" marker
  //   { role: "notice", content }               — a small inline notice
  // Only user/assistant items are sent back to the backend as history.
  const [messages, setMessages] = useState([]);
  // Whatever the user is currently typing in the input box.
  const [input, setInput] = useState("");
  // True while we're waiting for / receiving a reply (disables the send button).
  const [loading, setLoading] = useState(false);

  // The conversation we're in, remembered in localStorage. null until the first
  // reply, when the backend issues an id and returns it in the response header.
  const [conversationId, setConversationId] = useState(() =>
    localStorage.getItem(CONVERSATION_KEY)
  );

  // On first load, if we already have a conversation id, fetch its saved messages
  // so the chat resumes where it left off — the visible payoff of persistence.
  useEffect(() => {
    if (!conversationId) return;
    (async () => {
      try {
        const res = await fetch(`/api/conversations/${conversationId}`);
        if (!res.ok) return; // unknown id / backend down — just start fresh
        const data = await res.json();
        if (Array.isArray(data.messages) && data.messages.length) {
          setMessages(data.messages);
        }
      } catch {
        /* offline — start empty */
      }
    })();
    // Run once on mount only; we deliberately don't re-run when conversationId
    // changes mid-session (that would overwrite the live chat with the saved copy).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function sendMessage(e) {
    e.preventDefault(); // stop the form from reloading the page
    const text = input.trim();
    if (!text || loading) return;

    // Only real chat turns are conversation history; markers/notices are UI-only.
    const userMessage = { role: "user", content: text };
    const history = [...messages, userMessage].filter(
      (m) => m.role === "user" || m.role === "assistant"
    );
    setMessages([...messages, userMessage]);
    setInput("");
    setLoading(true);

    // The backend streams newline-delimited JSON events. Each agent-loop turn is
    // its own assistant bubble: `turn_end` (and `search`) close the current one
    // so the next bit of text opens a fresh bubble. `needNewBubble` tracks that.
    let needNewBubble = true;

    function handleEvent(evt) {
      if (evt.type === "text") {
        // Decide whether to open a new bubble BEFORE the async state update, so
        // the updater never reads the (possibly stale) mutable flag.
        const startNew = needNewBubble;
        needNewBubble = false;
        setMessages((prev) => {
          const updated = [...prev];
          if (startNew) {
            updated.push({ role: "assistant", content: evt.text });
          } else {
            const last = updated[updated.length - 1];
            updated[updated.length - 1] = { ...last, content: last.content + evt.text };
          }
          return updated;
        });
      } else if (evt.type === "turn_end") {
        needNewBubble = true; // next text starts a new bubble
      } else if (evt.type === "search") {
        setMessages((prev) => [
          ...prev,
          {
            role: "search",
            query: evt.query,
            max_results: evt.max_results,
            filters: evt.filters,
          },
        ]);
        needNewBubble = true;
      } else if (evt.type === "deep_research") {
        // A deep read began. Show one marker row that lists the PMIDs being read
        // and will fill in each paper as its sub-agent finishes.
        setMessages((prev) => [
          ...prev,
          { role: "deep_research", papers: evt.papers || [], goal: evt.goal, done: [] },
        ]);
        needNewBubble = true;
      } else if (evt.type === "deep_research_paper") {
        // One paper's sub-agent finished — record it on the most recent
        // deep_research marker so the row shows progress as papers complete.
        setMessages((prev) => {
          const updated = [...prev];
          for (let i = updated.length - 1; i >= 0; i--) {
            if (updated[i].role === "deep_research") {
              const done = [...updated[i].done, { pmid: evt.pmid, title: evt.title, source: evt.source }];
              updated[i] = { ...updated[i], done };
              break;
            }
          }
          return updated;
        });
        needNewBubble = true;
      } else if (evt.type === "notice") {
        setMessages((prev) => [...prev, { role: "notice", content: evt.text }]);
        needNewBubble = true;
      } else if (evt.type === "error") {
        setMessages((prev) => [...prev, { role: "notice", content: `⚠️ ${evt.text}` }]);
        needNewBubble = true;
      }
    }

    try {
      const response = await fetch(API_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: history, conversation_id: conversationId }),
      });

      // The backend issues (or echoes) the conversation id in a header; remember it
      // so later messages append to the same conversation and a refresh resumes it.
      const newId = response.headers.get("X-Conversation-Id");
      if (newId && newId !== conversationId) {
        setConversationId(newId);
        localStorage.setItem(CONVERSATION_KEY, newId);
      }

      // Read the stream and split it into whole JSON lines, buffering whatever
      // partial line is left at the end of each chunk for the next read.
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        const lines = buffer.split("\n");
        buffer = lines.pop(); // keep the trailing (possibly incomplete) line
        for (const line of lines) {
          if (!line.trim()) continue;
          try {
            handleEvent(JSON.parse(line));
          } catch {
            // Ignore a malformed line rather than killing the whole stream.
          }
        }
      }
      // Flush any final complete line left in the buffer.
      if (buffer.trim()) {
        try {
          handleEvent(JSON.parse(buffer));
        } catch {
          /* ignore */
        }
      }
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        { role: "notice", content: "⚠️ Error talking to the server. Is the backend running?" },
      ]);
    } finally {
      setLoading(false);
    }
  }

  // Reset to a blank conversation: clear the chat and forget the id, so the next
  // message starts a fresh conversation (the backend issues a new id for it; the
  // old conversation stays saved in DynamoDB).
  function startNewConversation() {
    setMessages([]);
    setConversationId(null);
    localStorage.removeItem(CONVERSATION_KEY);
    setInput("");
  }

  // Show a "working" indicator while we wait for the next bit of assistant text
  // (before the first token, and in the gap after a search before the answer).
  const last = messages[messages.length - 1];
  const showThinking = loading && (!last || last.role !== "assistant");

  return (
    <div className="app">
      <header className="app-header">
        <h1>Health Checker</h1>
        <button
          type="button"
          className="new-chat"
          onClick={startNewConversation}
          disabled={loading || messages.length === 0}
        >
          + New chat
        </button>
      </header>

      <div className="messages">
        {messages.length === 0 && (
          <p className="empty">Say hello to start the conversation 👋</p>
        )}
        {messages.map((m, i) => {
          if (m.role === "search") {
            const filterLabel = formatFilters(m.filters);
            return (
              <div key={i} className="message search">
                <div className="search-marker">
                  🔍 Searched PubMed: <span className="query">{m.query}</span>
                  <span className="count">
                    {" "}· up to {m.max_results} article{m.max_results === 1 ? "" : "s"}
                  </span>
                  {filterLabel && <span className="filters"> · {filterLabel}</span>}
                </div>
              </div>
            );
          }
          if (m.role === "deep_research") {
            const total = m.papers.length;
            const finished = m.done.length;
            return (
              <div key={i} className="message search">
                <div className="search-marker">
                  📄 Deep-reading {total} paper{total === 1 ? "" : "s"}
                  <span className="count">
                    {" "}· {finished}/{total} done
                  </span>
                  {m.done.length > 0 && (
                    <div className="deep-research-papers">
                      {m.done.map((p, j) => (
                        <div key={j} className="deep-research-paper">
                          ✓ PMID {p.pmid}
                          <span className="filters">
                            {" "}· {p.source === "full_text"
                              ? "full text"
                              : p.source === "no_full_text"
                              ? "no full text"
                              : p.source === "unavailable"
                              ? "not found"
                              : "error"}
                          </span>
                          {p.title && <span className="query"> — {p.title}</span>}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            );
          }
          if (m.role === "notice") {
            return (
              <div key={i} className="message notice">
                <div className="notice-marker">{m.content}</div>
              </div>
            );
          }
          return (
            <div key={i} className={`message ${m.role}`}>
              <span className="role">{m.role === "user" ? "You" : "Assistant"}</span>
              <div className="bubble">
                {m.content
                  ? m.role === "assistant"
                    ? stripMarkdown(m.content)
                    : m.content
                  : "…"}
              </div>
            </div>
          );
        })}
        {showThinking && (
          <div className="message assistant">
            <span className="role">Assistant</span>
            <div className="thinking">Thinking</div>
          </div>
        )}
      </div>

      <form className="input-row" onSubmit={sendMessage}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Type a message…"
          disabled={loading}
        />
        <button type="submit" disabled={loading || !input.trim()}>
          {loading ? "…" : "Send"}
        </button>
      </form>
    </div>
  );
}
