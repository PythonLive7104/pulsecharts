"""Signal-generation prompt (adapted from Section 20.2 for the OpenAI path).

The LLM is asked only for the *judgment* — direction, confidence, and the
plain-English reasoning/invalidation. Entry, stop loss, take-profits, and dollar
figures are computed deterministically afterward (levels.py, Section 19.2).
"""

SYSTEM_PROMPT = (
    "You are a professional quantitative crypto trading analyst. You analyze "
    "technical indicator data and decide whether a specific strategy presents a "
    "clear, high-confidence trade setup. You respond strictly in the structured "
    "JSON format requested. If the data does not present a clear high-confidence "
    "setup, set direction to NEUTRAL and confidence_pct below 65. These outputs "
    "are informational/algorithmic analysis, not financial advice."
)

# JSON schema for the judgment (used with OpenAI structured outputs, strict).
JUDGMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "direction": {"type": "string", "enum": ["BUY", "SELL", "NEUTRAL"]},
        "confidence_pct": {"type": "integer"},  # 0–100; range validated in code
        "reasoning": {"type": "string"},
        "invalidation": {"type": "string"},
    },
    "required": ["direction", "confidence_pct", "reasoning", "invalidation"],
    "additionalProperties": False,
}


# --- Hybrid mode: rule-based picks the direction, the LLM only annotates it ---

ANNOTATE_SYSTEM_PROMPT = (
    "You are a professional quantitative crypto trading analyst. Our rule-based "
    "system has ALREADY decided the direction of a trade setup. Your job is NOT "
    "to second-guess or change the direction — it is to explain it in plain "
    "English, state what would invalidate it, and rate how strong the setup looks "
    "(confidence 0–100) given the indicators. Respond strictly in the structured "
    "JSON format requested. These outputs are informational/algorithmic analysis, "
    "not financial advice."
)

ANNOTATION_SCHEMA = {
    "type": "object",
    "properties": {
        "confidence_pct": {"type": "integer"},  # 0–100; range validated in code
        "reasoning": {"type": "string"},
        "invalidation": {"type": "string"},
    },
    "required": ["confidence_pct", "reasoning", "invalidation"],
    "additionalProperties": False,
}


def _fmt(v) -> str:
    if v is None:
        return "n/a"
    return f"{v:.6g}" if isinstance(v, float) else str(v)


def build_user_prompt(symbol, timeframe, strategy_name, strategy_focus, ind: dict) -> str:
    return f"""Analyze the following technical data for {symbol} on the {timeframe} timeframe and evaluate a {strategy_name} signal.

CURRENT MARKET DATA:
- Symbol: {symbol}
- Timeframe: {timeframe}
- Current price (close): {_fmt(ind.get("close"))}
- 24h high: {_fmt(ind.get("high_24h"))}
- 24h low: {_fmt(ind.get("low_24h"))}
- Volume (current candle): {_fmt(ind.get("volume"))}
- Volume MA (20): {_fmt(ind.get("volume_ma20"))}

INDICATOR VALUES (most recent completed candle):
- EMA 9: {_fmt(ind.get("ema9"))}
- EMA 21: {_fmt(ind.get("ema21"))}
- EMA 50: {_fmt(ind.get("ema50"))}
- RSI (14): {_fmt(ind.get("rsi"))}
- MACD line: {_fmt(ind.get("macd_line"))}
- MACD signal: {_fmt(ind.get("macd_signal"))}
- MACD histogram: {_fmt(ind.get("macd_hist"))}
- Bollinger Upper: {_fmt(ind.get("bb_upper"))}
- Bollinger Middle: {_fmt(ind.get("bb_mid"))}
- Bollinger Lower: {_fmt(ind.get("bb_lower"))}
- ATR (14): {_fmt(ind.get("atr"))}
- Stochastic %K: {_fmt(ind.get("stoch_k"))}
- Stochastic %D: {_fmt(ind.get("stoch_d"))}
- VWAP: {_fmt(ind.get("vwap"))}

RECENT SWING LEVELS (last 50 candles):
- Nearest swing high: {_fmt(ind.get("swing_high"))}
- Nearest swing low: {_fmt(ind.get("swing_low"))}

STRATEGY FOCUS: {strategy_focus}

TASK:
1. Evaluate whether the data presents a clear {strategy_name} signal.
2. Determine direction: BUY, SELL, or NEUTRAL.
3. Assign confidence_pct (0–100) based on how strongly the conditions align. Only call a directional signal if confidence is >= 65; otherwise return NEUTRAL.
4. Write a 2–3 sentence plain-English reasoning string.
5. Write one sentence describing what would invalidate this signal.

Entry price is the current close ({_fmt(ind.get("close"))}); stop-loss and take-profit levels are computed separately, so do not include them."""


def build_annotate_prompt(symbol, timeframe, strategy_name, strategy_focus, direction, ind: dict) -> str:
    """Hybrid prompt: the direction is fixed by our rules; the LLM only explains it."""
    return f"""Our rule-based system has flagged a {direction} signal for {symbol} on the {timeframe} timeframe using the {strategy_name} strategy. The direction ({direction}) is already decided — do not change it.

CURRENT MARKET DATA:
- Symbol: {symbol}
- Timeframe: {timeframe}
- Current price (close): {_fmt(ind.get("close"))}
- Volume (current candle): {_fmt(ind.get("volume"))}
- Volume MA (20): {_fmt(ind.get("volume_ma20"))}

INDICATOR VALUES (most recent completed candle):
- EMA 9: {_fmt(ind.get("ema9"))}
- EMA 21: {_fmt(ind.get("ema21"))}
- EMA 50: {_fmt(ind.get("ema50"))}
- RSI (14): {_fmt(ind.get("rsi"))}
- MACD line: {_fmt(ind.get("macd_line"))}
- MACD signal: {_fmt(ind.get("macd_signal"))}
- MACD histogram: {_fmt(ind.get("macd_hist"))}
- Bollinger Upper: {_fmt(ind.get("bb_upper"))}
- Bollinger Middle: {_fmt(ind.get("bb_mid"))}
- Bollinger Lower: {_fmt(ind.get("bb_lower"))}
- ATR (14): {_fmt(ind.get("atr"))}
- Stochastic %K: {_fmt(ind.get("stoch_k"))}
- Stochastic %D: {_fmt(ind.get("stoch_d"))}
- VWAP: {_fmt(ind.get("vwap"))}

RECENT SWING LEVELS (last 50 candles):
- Nearest swing high: {_fmt(ind.get("swing_high"))}
- Nearest swing low: {_fmt(ind.get("swing_low"))}

STRATEGY FOCUS: {strategy_focus}

TASK (the {direction} direction is fixed — do not change it):
1. Write a 2–3 sentence plain-English reasoning string explaining why this {direction} setup is reasonable given the indicators.
2. Write one sentence describing what would invalidate this signal.
3. Rate confidence_pct (0–100): how strongly do the indicators support this {direction} setup?"""
