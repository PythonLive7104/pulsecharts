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
from .pregate import candidate_direction, confidence_score, passes_pregate
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


def generate_signal(
    symbol, timeframe, strategy_slug, strategy_name, strategy_focus, indicators,
    *, min_confidence=None, use_pregate=None, stats=None,
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

    # Hybrid mode: the rules pick the direction and the LLM only annotates it.
    if settings.SIGNAL_ENGINE_MODE == "hybrid":
        return _hybrid_signal(
            symbol, timeframe, strategy_slug, strategy_name, strategy_focus, indicators, bump
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

    entry = float(indicators["close"])
    levels = compute_levels(
        direction, entry, float(indicators["atr"]),
        float(indicators["swing_high"]), float(indicators["swing_low"]),
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


def _hybrid_signal(symbol, timeframe, strategy_slug, strategy_name, strategy_focus, indicators, bump):
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
