// Pro-only: describe a strategy in a sentence, the AI turns it into a rule you can
// follow. Two steps — Interpret (preview, no quota spent) then Create & follow.
import { useState } from "react";
import { api } from "../api";

export default function StrategyBuilder({ quota, onCreated, onClose }) {
  const [text, setText] = useState("");
  const [name, setName] = useState("");
  const [preview, setPreview] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  const remaining = quota?.remaining ?? 0;

  async function interpret() {
    setError(null);
    setBusy(true);
    try {
      const res = await api.previewStrategy(text);
      setPreview(res);
      setName(res.name || "");
    } catch (e) {
      setError(e.message);
      setPreview(null);
    } finally {
      setBusy(false);
    }
  }

  async function create() {
    setError(null);
    setBusy(true);
    try {
      const res = await api.createStrategy(text, name);
      onCreated(res);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="reminder-backdrop" role="dialog" aria-modal="true" onClick={onClose}>
      <div className="reminder-card strategy-builder" onClick={(e) => e.stopPropagation()}>
        <button className="reminder-x" aria-label="Close" onClick={onClose}>✕</button>
        <h2>Create your own strategy</h2>
        <p className="reminder-text" style={{ marginBottom: 12 }}>
          Describe it in one sentence — the AI turns it into rules using price, EMAs, RSI,
          MACD, Bollinger Bands, ADX, volume and VWAP.
        </p>

        <textarea
          className="sb-text"
          rows={3}
          placeholder="e.g. Buy when RSI drops below 30 and price is above the 200 EMA"
          value={text}
          onChange={(e) => { setText(e.target.value); setPreview(null); }}
          maxLength={500}
        />

        {error && <p className="error">{error}</p>}

        {preview && (
          <div className="sb-preview">
            <label className="sb-label">Name</label>
            <input className="sb-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={80} />
            <label className="sb-label">What this does</label>
            <p className="sb-summary">{preview.summary}</p>
          </div>
        )}

        <p className="sb-disclaimer">
          Custom strategies aren't backtested. Informational only, not financial advice.
        </p>

        <div className="reminder-actions">
          {!preview ? (
            <button className="btn-primary" onClick={interpret} disabled={busy || !text.trim()}>
              {busy ? "Interpreting…" : "Interpret"}
            </button>
          ) : (
            <button className="btn-primary" onClick={create} disabled={busy || remaining <= 0}>
              {busy ? "Creating…" : "Create & follow"}
            </button>
          )}
          <button className="btn-ghost" onClick={onClose}>Cancel</button>
        </div>

        <p className="muted sb-quota">
          {remaining} of {quota?.limit ?? 0} left this month · deleting one doesn't free a slot
        </p>
      </div>
    </div>
  );
}
