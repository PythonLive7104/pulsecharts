// Price display precision by asset class. Forex quotes need far more decimals
// than crypto: FX majors move in 0.0001 (pips) and quote to 5 dp, JPY pairs to
// 3 dp. Crypto keeps the existing 2-dp behaviour.

export function priceDecimals(assetClass, ticker, value) {
  // Metals (gold/silver) quote in dollars, not pips — keep 2 dp despite riding
  // the forex feed; the 5-dp FX default would be wrong for a ~$4000 price.
  if (/XAU|XAG/.test(ticker || "")) return 2;
  if (assetClass === "forex") return /JPY/.test(ticker || "") ? 3 : 5;

  // Crypto: precision by MAGNITUDE, not a fixed 2 dp. A flat 2 dp collapsed every
  // level of a sub-dollar coin onto the same number — INIT at $0.05 rendered entry,
  // stop, TP1, TP2 and TP3 all as "0.05", which is not a tradeable card. Mirrors the
  // Telegram formatter (tasks._fmt_price) so the bot and the web card agree.
  const a = Math.abs(Number(value) || 0);
  if (a >= 100) return 2;
  if (a >= 1) return 4;
  if (a >= 0.01) return 5;
  return 8; // sub-cent coins (kPEPE et al)
}

export function formatPrice(n, assetClass, ticker) {
  if (n == null) return "—";
  const d = priceDecimals(assetClass, ticker, n);
  return Number(n).toLocaleString(undefined, {
    minimumFractionDigits: d,
    maximumFractionDigits: d,
  });
}
