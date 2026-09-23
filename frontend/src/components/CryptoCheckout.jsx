import { useEffect, useState } from "react";
import { api } from "../api";

/**
 * Pay-by-crypto checkout.
 *
 * Paystack declined this account (trading sites are outside their acceptable-use
 * policy), so payment is a direct transfer to a wallet, reviewed by staff.
 *
 * Two things this UI must get right, because both fail expensively:
 *  - the NETWORK is shown as prominently as the address. USDT exists on several
 *    chains and paying on the wrong one loses the money irrecoverably.
 *  - it never implies the plan is active. Submitting a hash opens a review; the
 *    copy says "submitted for review", not "payment received".
 */
export default function CryptoCheckout({ plan, planLabel, priceUsd, onClose }) {
  const [wallets, setWallets] = useState(null);
  const [asset, setAsset] = useState(null);
  const [txHash, setTxHash] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const [done, setDone] = useState(false);
  const [copied, setCopied] = useState("");

  useEffect(() => {
    let alive = true;
    api
      .cryptoWallets()
      .then((d) => {
        if (!alive) return;
        setWallets(d.wallets || []);
        setAsset((d.wallets || [])[0]?.asset || null);
      })
      .catch(() => alive && setWallets([]));
    return () => {
      alive = false;
    };
  }, []);

  const active = (wallets || []).find((w) => w.asset === asset);

  async function copy(text) {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(text);
      setTimeout(() => setCopied(""), 1800);
    } catch {
      /* clipboard can be blocked; the address is selectable either way */
    }
  }

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    try {
      await api.cryptoClaim({ plan, asset, tx_hash: txHash.trim() });
      setDone(true);
    } catch (err) {
      setMsg(err.message || "Couldn't submit that — please check the hash.");
    } finally {
      setBusy(false);
    }
  }

  if (wallets === null) {
    return <div className="cc-wrap"><p className="muted">Loading payment details…</p></div>;
  }
  if (!wallets.length) {
    return (
      <div className="cc-wrap">
        <p>Crypto payment isn't available right now. Please contact support.</p>
        <button type="button" onClick={onClose}>Close</button>
      </div>
    );
  }

  if (done) {
    return (
      <div className="cc-wrap cc-done">
        <div className="cc-tick" aria-hidden="true">✓</div>
        <h3>Submitted for review</h3>
        <p className="muted">
          We'll confirm your transaction on-chain and activate {planLabel} on this
          account. You'll get an email as soon as it's live — usually within a few
          hours.
        </p>
        <button type="button" className="cc-primary" onClick={onClose}>Done</button>
      </div>
    );
  }

  return (
    <div className="cc-wrap">
      <div className="cc-head">
        <div>
          <h3>Pay with crypto</h3>
          <p className="muted">{planLabel} — <b>${priceUsd}</b></p>
        </div>
        <button type="button" className="cc-close" onClick={onClose} aria-label="Close">×</button>
      </div>

      <div className="cc-assets" role="tablist">
        {wallets.map((w) => (
          <button
            key={w.asset}
            type="button"
            role="tab"
            aria-selected={w.asset === asset}
            className={`cc-asset ${w.asset === asset ? "on" : ""}`}
            onClick={() => setAsset(w.asset)}
          >
            {w.asset}
          </button>
        ))}
      </div>

      {active && (
        <div className="cc-pay">
          <div className="cc-qr" dangerouslySetInnerHTML={{ __html: active.qr_svg }} />
          <div className="cc-details">
            {/* Network is a warning, not a label — wrong chain means lost funds. */}
            <div className="cc-network">
              <span className="cc-net-tag">Network</span>
              <b>{active.network}</b>
            </div>
            <label className="cc-label" htmlFor="cc-addr">Send exactly ${priceUsd} to</label>
            <div className="cc-addr-row">
              <code id="cc-addr" className="cc-addr">{active.address}</code>
              <button type="button" onClick={() => copy(active.address)}>
                {copied === active.address ? "Copied" : "Copy"}
              </button>
            </div>
            <p className="cc-warn">
              Send only <b>{active.asset}</b> on the <b>{active.network}</b> network.
              Anything else will be lost.
            </p>
          </div>
        </div>
      )}

      <form className="cc-form" onSubmit={submit}>
        <label className="cc-label" htmlFor="cc-tx">Transaction hash</label>
        <input
          id="cc-tx"
          value={txHash}
          onChange={(e) => setTxHash(e.target.value)}
          placeholder="Paste the transaction hash from your wallet"
          autoComplete="off"
        />
        {msg && <p className="cc-err">{msg}</p>}
        <button type="submit" className="cc-primary" disabled={busy || txHash.trim().length < 10}>
          {busy ? "Submitting…" : "I've paid — submit for review"}
        </button>
        <p className="muted cc-foot">
          Your plan activates once we've confirmed the transaction. Nothing is charged
          automatically and no card details are stored.
        </p>
      </form>
    </div>
  );
}
