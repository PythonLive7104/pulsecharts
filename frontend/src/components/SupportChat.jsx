// Landing-page support chat widget (lower-right). Answers come from the backend
// curated knowledge base (no LLM). When a question can't be matched, the visitor
// is offered a contact form that emails the team. Rendered only on LandingPage.
import { useEffect, useRef, useState } from "react";
import { api } from "../api";

const GREETING = {
  from: "bot",
  text: "Hi! 👋 Ask me anything about PulseCharts — pricing, features, indicators, data, or signals.",
};

export default function SupportChat() {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState([GREETING]);
  const [suggestions, setSuggestions] = useState([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [showContact, setShowContact] = useState(false);
  const scrollRef = useRef(null);

  // Fetch starter chips the first time the panel opens.
  useEffect(() => {
    if (open && suggestions.length === 0) {
      api.supportSuggestions()
        .then((d) => setSuggestions(d?.suggestions || []))
        .catch(() => { /* chips are optional */ });
    }
  }, [open, suggestions.length]);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [messages, showContact, open]);

  async function ask(question) {
    const q = question.trim();
    if (!q || sending) return;
    setInput("");
    setMessages((m) => [...m, { from: "user", text: q }]);
    setSending(true);
    try {
      const res = await api.supportChat(q);
      setMessages((m) => [...m, { from: "bot", text: res.reply }]);
      if (!res.matched) setShowContact(true);
    } catch {
      setMessages((m) => [
        ...m,
        { from: "bot", text: "Something went wrong. You can reach us via the contact option below." },
      ]);
      setShowContact(true);
    } finally {
      setSending(false);
    }
  }

  return (
    <>
      <button
        className="support-fab"
        aria-label={open ? "Close chat" : "Open chat"}
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        {open ? "✕" : "💬"}
      </button>

      {open && (
        <div className="support-panel" role="dialog" aria-label="PulseCharts help chat">
          <div className="support-head">
            <span className="support-title">📈 PulseCharts Help</span>
            <button className="support-close" aria-label="Close" onClick={() => setOpen(false)}>✕</button>
          </div>

          <div className="support-body" ref={scrollRef}>
            {messages.map((m, i) => (
              <div key={i} className={`support-msg ${m.from}`}>{m.text}</div>
            ))}
            {sending && <div className="support-msg bot support-typing">…</div>}

            {!sending && suggestions.length > 0 && messages.length <= 2 && (
              <div className="support-chips">
                {suggestions.map((s) => (
                  <button key={s} className="support-chip" onClick={() => ask(s)}>{s}</button>
                ))}
              </div>
            )}

            {showContact && <ContactForm onClose={() => setShowContact(false)} />}
          </div>

          <form
            className="support-input"
            onSubmit={(e) => { e.preventDefault(); ask(input); }}
          >
            <input
              type="text"
              value={input}
              placeholder="Type your question…"
              onChange={(e) => setInput(e.target.value)}
              disabled={sending}
            />
            <button type="submit" disabled={sending || !input.trim()}>Send</button>
          </form>

          <button className="support-contact-link" onClick={() => setShowContact((v) => !v)}>
            Can't find an answer? Contact us
          </button>
        </div>
      )}
    </>
  );
}

function ContactForm({ onClose }) {
  const [email, setEmail] = useState("");
  const [message, setMessage] = useState("");
  const [state, setState] = useState("idle"); // idle | sending | done | error
  const [error, setError] = useState("");

  async function submit(e) {
    e.preventDefault();
    if (state === "sending") return;
    setState("sending");
    setError("");
    try {
      const res = await api.supportContact(email, message);
      setState("done");
      setMessage("");
    } catch (err) {
      setError(err.message || "Couldn't send. Please try again.");
      setState("error");
    }
  }

  if (state === "done") {
    return (
      <div className="support-contact-card">
        <p className="support-contact-done">✓ Thanks! We'll get back to you by email soon.</p>
        <button className="support-chip" onClick={onClose}>Close</button>
      </div>
    );
  }

  return (
    <form className="support-contact-card" onSubmit={submit}>
      <p className="support-contact-head">Send us a message</p>
      <input
        type="email"
        required
        placeholder="Your email"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
      />
      <textarea
        required
        rows={3}
        placeholder="How can we help?"
        value={message}
        onChange={(e) => setMessage(e.target.value)}
      />
      {error && <p className="support-contact-err">{error}</p>}
      <button type="submit" disabled={state === "sending"}>
        {state === "sending" ? "Sending…" : "Send message"}
      </button>
    </form>
  );
}
