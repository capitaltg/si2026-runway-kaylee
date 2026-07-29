import React, { useEffect, useRef, useState } from "react";
import { askRunway, listContracts } from "../api.js";

const grotesk = "'Space Grotesk',sans-serif";

// Suggested openers. Portfolio-level always show; contract-level are added when a
// contract is open so "this contract" resolves to it on the backend.
const PORTFOLIO_CHIPS = [
  "Which contracts are most at risk?",
  "What's my total weekly burn across the portfolio?",
  "Rank my contracts by weeks of runway.",
];
const CONTRACT_CHIPS = [
  "How many weeks of runway do I have?",
  "When does funding run dry?",
  "Am I over-obligating on any CLIN?",
];

const chipStyle = {
  padding: "8px 14px",
  borderRadius: 20,
  border: "1px solid var(--border)",
  background: "var(--panel)",
  color: "var(--dim)",
  fontSize: 12.5,
  fontWeight: 600,
  cursor: "pointer",
  textAlign: "left",
};

// Render **bold** spans (and hide stray asterisks) so the model's occasional
// markdown never shows up as literal ** in the chat. Complete pairs become bold;
// an unclosed ** mid-stream just renders as text until its partner arrives.
function renderRich(text) {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, i) => {
    const m = /^\*\*([^*]+)\*\*$/.exec(part);
    return m ? <strong key={i}>{m[1]}</strong> : <React.Fragment key={i}>{part}</React.Fragment>;
  });
}

function Bubble({ role, content, pending }) {
  const isUser = role === "user";
  return (
    <div
      style={{
        display: "flex",
        justifyContent: isUser ? "flex-end" : "flex-start",
        marginBottom: 12,
      }}
    >
      <div
        style={{
          maxWidth: "85%",
          padding: "11px 15px",
          borderRadius: 14,
          borderBottomRightRadius: isUser ? 4 : 14,
          borderBottomLeftRadius: isUser ? 14 : 4,
          background: isUser ? "var(--accent)" : "var(--panel)",
          border: isUser ? "none" : "1px solid var(--border)",
          color: isUser ? "#fff" : "var(--text)",
          fontSize: 13.5,
          lineHeight: 1.55,
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
          boxShadow: isUser ? "0 4px 12px rgba(67,97,238,.22)" : "none",
        }}
      >
        {content
          ? isUser
            ? content
            : renderRich(content)
          : pending
            ? <span style={{ color: "var(--faint)" }}>…</span>
            : ""}
      </div>
    </div>
  );
}

// Ask Runway lives in a right-side slide-out drawer (Claude-style), overlaid on
// whatever view is open — triggered by the top bar's "Ask Runway" button.
export default function AskRunway({ open, onClose, contractId }) {
  const [contracts, setContracts] = useState(null);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState(null);
  const threadRef = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => {
    listContracts()
      .then(setContracts)
      .catch(() => setContracts([]));
  }, []);

  // Esc closes; focus the composer when the drawer opens.
  useEffect(() => {
    if (!open) return;
    const onKey = (e) => e.key === "Escape" && onClose?.();
    window.addEventListener("keydown", onKey);
    const t = setTimeout(() => inputRef.current?.focus(), 60);
    return () => {
      window.removeEventListener("keydown", onKey);
      clearTimeout(t);
    };
  }, [open, onClose]);

  // Keep the newest message in view as it streams in.
  useEffect(() => {
    const el = threadRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  const active = contracts?.find((c) => c.id === contractId);

  async function send(text) {
    const q = (text ?? input).trim();
    if (!q || streaming) return;
    setInput("");
    setError(null);
    // History = the completed turns before this one (the backend re-attaches the
    // live numbers itself, so we only send the conversation text).
    const history = messages.map((m) => ({ role: m.role, content: m.content }));
    setMessages((m) => [
      ...m,
      { role: "user", content: q },
      { role: "assistant", content: "" },
    ]);
    setStreaming(true);
    try {
      await askRunway({ question: q, history, contractId }, (chunk) => {
        setMessages((m) => {
          const copy = m.slice();
          const last = copy[copy.length - 1];
          copy[copy.length - 1] = { ...last, content: last.content + chunk };
          return copy;
        });
      });
    } catch (e) {
      setError(e.message);
      // Drop the empty assistant bubble if nothing streamed before the failure.
      setMessages((m) => {
        const copy = m.slice();
        const last = copy[copy.length - 1];
        if (last?.role === "assistant" && !last.content) copy.pop();
        return copy;
      });
    } finally {
      setStreaming(false);
      inputRef.current?.focus();
    }
  }

  const hasContracts = contracts == null || contracts.length > 0;
  const chips = [...(contractId ? CONTRACT_CHIPS : []), ...PORTFOLIO_CHIPS];

  return (
    <>
      {/* scrim */}
      <div
        onClick={onClose}
        aria-hidden
        style={{
          position: "fixed",
          inset: 0,
          background: "rgba(15,20,35,.35)",
          opacity: open ? 1 : 0,
          pointerEvents: open ? "auto" : "none",
          transition: "opacity .25s ease",
          zIndex: 200,
        }}
      />

      {/* drawer */}
      <aside
        role="dialog"
        aria-label="Ask Runway"
        style={{
          position: "fixed",
          top: 0,
          right: 0,
          bottom: 0,
          width: "min(440px, 92vw)",
          background: "var(--bg)",
          borderLeft: "1px solid var(--border)",
          boxShadow: "-18px 0 44px rgba(15,20,35,.22)",
          transform: open ? "translateX(0)" : "translateX(100%)",
          transition: "transform .28s cubic-bezier(.4,0,.2,1)",
          zIndex: 201,
          display: "flex",
          flexDirection: "column",
        }}
      >
        {/* header */}
        <div
          style={{
            display: "flex",
            alignItems: "flex-start",
            gap: 12,
            padding: "18px 20px 14px",
            borderBottom: "1px solid var(--border)",
          }}
        >
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" strokeWidth="1.9">
                <path d="M21 11.5a8.4 8.4 0 01-11.9 7.6L3 21l1.9-6A8.4 8.4 0 1121 11.5z" strokeLinejoin="round" />
              </svg>
              <span style={{ fontFamily: grotesk, fontSize: 16, fontWeight: 600, color: "var(--text)" }}>
                Ask Runway
              </span>
            </div>
            <div style={{ fontSize: 12, color: "var(--dim)", marginTop: 4 }}>
              {active ? (
                <>
                  Live burn &amp; funding for{" "}
                  <b>{active.nickname || active.contract?.contractor || active.piid}</b> — and
                  your whole portfolio.
                </>
              ) : (
                <>Live burn, runway &amp; funding across every contract.</>
              )}
            </div>
          </div>
          <button
            onClick={onClose}
            title="Close"
            style={{
              width: 30,
              height: 30,
              flexShrink: 0,
              borderRadius: 8,
              border: "1px solid var(--border)",
              background: "var(--panel2)",
              color: "var(--dim)",
              cursor: "pointer",
              fontSize: 16,
              lineHeight: 1,
            }}
          >
            ×
          </button>
        </div>

        {error && (
          <div style={{ margin: "12px 16px 0", color: "var(--bad)", fontSize: 12.5 }}>
            {error}
          </div>
        )}

        {/* thread */}
        <div ref={threadRef} style={{ flex: 1, overflowY: "auto", minHeight: 0, padding: "16px 16px 4px" }}>
          {messages.length === 0 ? (
            !hasContracts ? (
              <div style={{ color: "var(--dim)", fontSize: 13, lineHeight: 1.5 }}>
                No contracts ingested yet — add one from{" "}
                <b style={{ color: "var(--text)" }}>Ingest</b> and I&apos;ll answer
                questions about its burn and funding.
              </div>
            ) : (
              <>
                <div
                  style={{
                    fontSize: 11,
                    letterSpacing: ".08em",
                    textTransform: "uppercase",
                    color: "var(--faint)",
                    fontWeight: 700,
                    marginBottom: 12,
                  }}
                >
                  Try asking
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: 9, alignItems: "flex-start" }}>
                  {chips.map((c) => (
                    <button key={c} onClick={() => send(c)} style={chipStyle}>
                      {c}
                    </button>
                  ))}
                </div>
              </>
            )
          ) : (
            messages.map((m, i) => (
              <Bubble
                key={i}
                role={m.role}
                content={m.content}
                pending={streaming && i === messages.length - 1}
              />
            ))
          )}
        </div>

        {/* composer */}
        <div style={{ display: "flex", gap: 8, padding: "12px 16px 16px", borderTop: "1px solid var(--border)", alignItems: "flex-end" }}>
          <textarea
            ref={inputRef}
            rows={1}
            value={input}
            disabled={!hasContracts}
            placeholder="Ask about your burn, runway, or funding…"
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
            style={{
              flex: 1,
              resize: "none",
              maxHeight: 120,
              padding: "10px 13px",
              borderRadius: 12,
              border: "1px solid var(--border)",
              background: "var(--inputBg)",
              color: "var(--text)",
              fontSize: 13.5,
              fontFamily: "inherit",
              lineHeight: 1.5,
            }}
          />
          <button
            onClick={() => send()}
            disabled={streaming || !input.trim()}
            style={{
              height: 40,
              padding: "0 18px",
              borderRadius: 12,
              border: "none",
              background: "var(--accent)",
              color: "#fff",
              fontWeight: 600,
              fontSize: 13.5,
              cursor: streaming || !input.trim() ? "default" : "pointer",
              opacity: streaming || !input.trim() ? 0.5 : 1,
              boxShadow: "0 4px 12px rgba(67,97,238,.28)",
            }}
          >
            {streaming ? "…" : "Ask"}
          </button>
        </div>
      </aside>
    </>
  );
}
