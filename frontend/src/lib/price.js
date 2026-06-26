// Price display precision by asset class. Forex quotes need far more decimals
// than crypto: FX majors move in 0.0001 (pips) and quote to 5 dp, JPY pairs to
// 3 dp. Crypto keeps the existing 2-dp behaviour.

export function priceDecimals(assetClass, ticker) {
  // Metals (gold/silver) quote in dollars, not pips — keep 2 dp despite riding
  // the forex feed; the 5-dp FX default would be wrong for a ~$4000 price.
  if (/XAU|XAG/.test(ticker || "")) return 2;
  if (assetClass === "forex") return /JPY/.test(ticker || "") ? 3 : 5;
  return 2; // crypto / default
}

export function formatPrice(n, assetClass, ticker) {
  if (n == null) return "—";
  const d = priceDecimals(assetClass, ticker);
  return Number(n).toLocaleString(undefined, {
    // Forex shows a fixed number of decimals; crypto stays variable (≤2).
    minimumFractionDigits: assetClass === "forex" ? d : 0,
    maximumFractionDigits: d,
  });
}
