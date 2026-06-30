"""Signal generation engine (OpenAI path).

Pipeline (Section 20.1, adapted to OpenAI):
    indicators (computed server-side) -> LLM judgment (direction/confidence/
    reasoning/invalidation) -> deterministic TP/SL math -> Signal dict.

The LLM call uses OpenAI structured outputs (json_schema, strict) so the
response is guaranteed schema-valid JSON — no markdown-fence stripping needed.
"""

import json
import logging

from django.conf import settings

from .levels import compute_levels
from .pregate import candidate_direction, confidence_score, passes_ema_gate, passes_pregate
from .prompt import (
    ANNOTATION_SCHEMA,
    ANNOTATE_SYSTEM_PROMPT,
    JUDGMENT_SCHEMA,
    SYSTEM_PROMPT,
    build_annotate_prompt,
    build_user_prompt,
)

logger = logging.getLogger("signals.engine")


class SignalEngineError(RuntimeError):
    pass


def _client():
    from openai import OpenAI  # imported lazily so the app loads without the key

    if not settings.OPENAI_API_KEY:
        raise SignalEngineError("OPENAI_API_KEY is not set.")
    kwargs = {"api_key": settings.OPENAI_API_KEY}
    if settings.OPENAI_BASE_URL:
        kwargs["base_url"] = settings.OPENAI_BASE_URL
    return OpenAI(**kwargs)


# Room for the small JSON object so the response is never truncated mid-string.
_LLM_MAX_TOKENS = 700


def _parse_llm_json(resp, ctx: str) -> dict:
    """Tolerantly parse the LLM's JSON content. Returns {} (never raises) on a
    truncated/garbled response, so a single bad reply degrades gracefully rather
    than crashing the scan."""
    choice = resp.choices[0]
    content = (choice.message.content or "").strip()
    if getattr(choice, "finish_reason", None) == "length":
        logger.warning("LLM %s response hit the token limit (truncated).", ctx)
    if not content:
        return {}
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        cleaned = content.replace("```json", "").replace("```", "").strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            logger.warning("LLM %s returned unparseable JSON: %.150r", ctx, content)
            return {}


def generate_judgment(symbol, timeframe, strategy_name, strategy_focus, indicators):
    """Call the LLM for a structured judgment. Returns (parsed_dict, usage)."""
    prompt = build_user_prompt(symbol, timeframe, strategy_name, strategy_focus, indicators)
    resp = _client().chat.completions.create(
        model=settings.OPENAI_MODEL,
        temperature=settings.SIGNAL_TEMPERATURE,
        max_tokens=_LLM_MAX_TOKENS,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "signal_judgment",
                "strict": True,
                "schema": JUDGMENT_SCHEMA,
            },
        },
    )
    return _parse_llm_json(resp, "judgment"), getattr(resp, "usage", None)


def generate_annotation(symbol, timeframe, strategy_name, strategy_focus, direction, indicators):
    """Hybrid mode: rules already chose `direction`; the LLM only writes the
    reasoning/invalidation and a confidence read. Returns (parsed_dict, usage)."""
    prompt = build_annotate_prompt(symbol, timeframe, strategy_name, strategy_focus, direction, indicators)
    resp = _client().chat.completions.create(
        model=settings.OPENAI_MODEL,
        temperature=settings.SIGNAL_TEMPERATURE,
        max_tokens=_LLM_MAX_TOKENS,
        messages=[
            {"role": "system", "content": ANNOTATE_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "signal_annotation",
                "strict": True,
                "schema": ANNOTATION_SCHEMA,
            },
        },
    )
    return _parse_llm_json(resp, "annotation"), getattr(resp, "usage", None)


def _stop_mults(asset_class):
    """ATR (floor, cap) stop multipliers for this asset class — crypto wider than
    forex (settings.SIGNAL_ATR_STOP_FLOOR/CAP). Falls back to the crypto band for
    any unknown class."""
    floor = settings.SIGNAL_ATR_STOP_FLOOR.get(asset_class) or settings.SIGNAL_ATR_STOP_FLOOR["crypto"]
    cap = settings.SIGNAL_ATR_STOP_CAP.get(asset_class) or settings.SIGNAL_ATR_STOP_CAP["crypto"]
    return floor, cap


def generate_signal(
    symbol, timeframe, strategy_slug, strategy_name, strategy_focus, indicators,
    *, min_confidence=None, use_pregate=None, stats=None, asset_class="crypto",
):
    """Full pipeline. Returns a Signal field dict, or None if no qualifying setup.

    None when: indicators incomplete, the rule-based pre-gate rejects the snapshot
    (no LLM call made), the LLM says NEUTRAL / below threshold, or the levels are
    degenerate (no valid stop).

    `stats` (optional dict) accumulates: gated, llm_calls, in_tokens, out_tokens.
    `use_pregate` overrides settings.SIGNAL_PREGATE_ENABLED for this call.
    """
    def bump(key, n=1):
        if stats is not None:
            stats[key] = stats.get(key, 0) + n

    # Need ATR + swings to build the card.
    if indicators.get("atr") in (None, 0) or indicators.get("close") in (None, 0):
        return None

    # Cheap rule-based pre-gate — skips the paid LLM call on obvious non-setups.
    pregate_on = settings.SIGNAL_PREGATE_ENABLED if use_pregate is None else use_pregate
    if pregate_on and not passes_pregate(strategy_slug, indicators):
        bump("gated")
        return None

    stop_mults = _stop_mults(asset_class)

    # Rules mode: fully deterministic, no LLM call at all. The rule picks the
    # direction + confidence and the reasoning is templated. Zero per-signal cost,
    # which is what makes lower-timeframe / high-volume scanning economical.
    if settings.SIGNAL_ENGINE_MODE == "rules":
        return _rules_signal(
            symbol, timeframe, strategy_slug, strategy_name, strategy_focus, indicators, stop_mults
        )

    # Hybrid mode: the rules pick the direction and the LLM only annotates it.
    if settings.SIGNAL_ENGINE_MODE == "hybrid":
        return _hybrid_signal(
            symbol, timeframe, strategy_slug, strategy_name, strategy_focus, indicators, bump, stop_mults
        )

    # --- llm_gate (default): the LLM decides direction + confidence ---
    threshold = settings.SIGNAL_MIN_CONFIDENCE if min_confidence is None else min_confidence

    judgment, usage = generate_judgment(symbol, timeframe, strategy_name, strategy_focus, indicators)
    bump("llm_calls")
    if usage is not None:
        bump("in_tokens", getattr(usage, "prompt_tokens", 0) or 0)
        bump("out_tokens", getattr(usage, "completion_tokens", 0) or 0)
    direction = judgment.get("direction", "NEUTRAL")
    confidence = int(judgment.get("confidence_pct", 0))

    if direction == "NEUTRAL" or confidence < threshold:
        return None

    # Non-breakout signals must agree with the 9/21/200 EMA stack, even when the
    # LLM picked the direction — breakout strategies are exempt (passes_ema_gate).
    if not passes_ema_gate(strategy_slug, indicators, direction):
        return None

    entry = float(indicators["close"])
    levels = compute_levels(
        direction, entry, float(indicators["atr"]),
        float(indicators["swing_high"]), float(indicators["swing_low"]),
        atr_stop_mult=stop_mults[0], max_atr_mult=stop_mults[1],
    )
    if levels is None:
        return None

    return {
        "direction": direction,
        "confidence_pct": confidence,
        "entry_price": entry,
        "reasoning": judgment.get("reasoning", ""),
        "invalidation": judgment.get("invalidation", ""),
        **levels,
    }


def _p(v):
    """Format an indicator value with precision suited to its magnitude, so FX
    pairs near 0.5–1.5 don't collapse to '0.57' while gold/BTC stay readable."""
    if v is None:
        return "n/a"
    a = abs(v)
    return f"{v:,.2f}" if a >= 100 else (f"{v:.4f}" if a >= 1 else f"{v:.5f}")


def _checks(slug, ind, buy):
    """The parameters a strategy actually watched, rendered as confirming phrases.
    Mirrors each strategy's directional rule in pregate.py so the card shows exactly
    what was considered for the decision (no LLM)."""
    g = ind.get
    up = "above" if buy else "below"
    out = []

    def ema_cross():
        if g("ema9") is not None and g("ema21") is not None:
            out.append(f"EMA9 {_p(g('ema9'))} {'>' if buy else '<'} EMA21 {_p(g('ema21'))}")

    def price_ema200():
        if g("close") is not None and g("ema200") is not None:
            out.append(f"price {up} EMA200 ({_p(g('ema200'))})")

    def macd_hist():
        if g("macd_hist") is not None:
            out.append(f"MACD histogram {g('macd_hist'):+.4f} ({'bullish' if buy else 'bearish'})")

    def rsi_50():
        if g("rsi") is not None:
            out.append(f"RSI {g('rsi'):.0f} ({'≥' if buy else '≤'}50, momentum {'up' if buy else 'down'})")

    def rsi_pullback():
        if g("rsi") is not None:
            out.append(f"RSI {g('rsi'):.0f} (pullback into the trend)")

    def rsi_lvl():
        if g("rsi") is not None:
            out.append(f"RSI {g('rsi'):.0f}")

    def adx():
        if g("adx") is not None:
            out.append(f"ADX {g('adx'):.0f} (trend strength)")

    def price_vwap():
        if g("close") is not None and g("vwap") is not None:
            out.append(f"price {up} VWAP ({_p(g('vwap'))})")

    def price_ema9():
        if g("close") is not None and g("ema9") is not None:
            out.append(f"price {up} EMA9 ({_p(g('ema9'))})")

    def ribbon():
        out.append(f"EMA ribbon stacked {'9>21>200' if buy else '9<21<200'}")

    def range_break():
        lvl = g("swing_high") if buy else g("swing_low")
        if lvl is not None:
            out.append(f"price broke the recent {'high' if buy else 'low'} ({_p(lvl)})")

    def bb_break():
        out.append(f"price closed {'above the upper' if buy else 'below the lower'} Bollinger band")

    def volume():
        vol, vma = g("volume"), g("volume_ma20")
        if vol and vma:
            out.append(f"volume {vol / vma:.1f}× its 20-bar average")

    selection = {
        "momentum-crossover": (ema_cross, macd_hist, rsi_50),
        "macd-trend-following": (price_ema200, macd_hist),
        "volatility-breakout": (range_break, volume),
        "trend-rider": (price_ema200, ema_cross, rsi_50),
        "vwap-trend": (price_vwap, rsi_50),
        "bollinger-breakout": (bb_break, rsi_lvl, volume),
        "trend-pullback": (price_ema200, ema_cross, rsi_pullback),
        "ema-ribbon": (ribbon, price_ema9),
        "donchian-trend": (range_break, price_ema200),
        "adx-trend": (price_ema200, ema_cross, adx),
    }.get(slug, (ema_cross, rsi_50, adx))
    for fn in selection:
        fn()
    return out


def _rule_reasoning(strategy_slug, strategy_name, direction, indicators):
    """Deterministic reasoning/invalidation for the card — the parameters the
    strategy watched and how they lined up behind the call (no LLM)."""
    buy = direction == "BUY"
    checks = _checks(strategy_slug, indicators, buy)
    body = "; ".join(checks) if checks else "entry conditions aligned"
    reasoning = f"{strategy_name} {direction} setup — {body}."
    invalidation = (
        "A close back through the stop, or the EMA stack flipping against the "
        f"{'long' if buy else 'short'}, invalidates the setup."
    )
    return reasoning, invalidation


def _rules_signal(symbol, timeframe, strategy_slug, strategy_name, strategy_focus, indicators, stop_mults):
    """Pure rule-based signal — no LLM. Direction + confidence come from the
    deterministic rule (the edge the backtest measures); reasoning is templated."""
    direction = candidate_direction(strategy_slug, indicators)
    if direction not in ("BUY", "SELL"):
        return None

    entry = float(indicators["close"])
    levels = compute_levels(
        direction, entry, float(indicators["atr"]),
        float(indicators["swing_high"]), float(indicators["swing_low"]),
        atr_stop_mult=stop_mults[0], max_atr_mult=stop_mults[1],
    )
    if levels is None:
        return None

    confidence = confidence_score(direction, indicators) or settings.SIGNAL_RULE_CONFIDENCE
    reasoning, invalidation = _rule_reasoning(strategy_slug, strategy_name, direction, indicators)
    return {
        "direction": direction,
        "confidence_pct": confidence,
        "entry_price": entry,
        "reasoning": reasoning,
        "invalidation": invalidation,
        **levels,
    }


def _hybrid_signal(symbol, timeframe, strategy_slug, strategy_name, strategy_focus, indicators, bump, stop_mults):
    """Rule-based generates, LLM annotates.

    Direction comes from the deterministic rule (candidate_direction) — the same
    edge the backtest measures — so every plausible rule setup becomes a signal
    and is tracked. The LLM never gates: it only writes reasoning/invalidation and
    a confidence read for the card. If the LLM call fails, we still emit the
    rule-based signal (no reasoning, fallback confidence) rather than lose it.
    """
    direction = candidate_direction(strategy_slug, indicators)
    if direction not in ("BUY", "SELL"):
        return None

    entry = float(indicators["close"])
    levels = compute_levels(
        direction, entry, float(indicators["atr"]),
        float(indicators["swing_high"]), float(indicators["swing_low"]),
        atr_stop_mult=stop_mults[0], max_atr_mult=stop_mults[1],
    )
    if levels is None:
        return None

    # Confidence is the deterministic conviction score (how strongly indicators
    # align) — not the LLM's guess and not a win-rate. Varies per setup.
    confidence = confidence_score(direction, indicators) or settings.SIGNAL_RULE_CONFIDENCE

    reasoning, invalidation = "", ""
    try:
        ann, usage = generate_annotation(
            symbol, timeframe, strategy_name, strategy_focus, direction, indicators
        )
        bump("llm_calls")
        if usage is not None:
            bump("in_tokens", getattr(usage, "prompt_tokens", 0) or 0)
            bump("out_tokens", getattr(usage, "completion_tokens", 0) or 0)
        reasoning = ann.get("reasoning", "")
        invalidation = ann.get("invalidation", "")
    except Exception:
        logger.exception("annotation failed for %s %s %s — emitting rule-based signal without it",
                         symbol, timeframe, strategy_slug)

    return {
        "direction": direction,
        "confidence_pct": confidence,
        "entry_price": entry,
        "reasoning": reasoning,
        "invalidation": invalidation,
        **levels,
    }
