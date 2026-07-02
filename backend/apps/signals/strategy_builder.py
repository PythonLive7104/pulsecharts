"""User-defined (Pro) strategies: natural-language -> validated rule -> evaluation.

A Pro user describes a strategy in plain English. The LLM converts that sentence
into a declarative ``rule_config`` that references ONLY whitelisted indicator keys
and a fixed set of comparison operators. The deterministic engine then evaluates
that rule during scans exactly like a built-in strategy — no per-signal LLM cost,
no code execution, nothing off the whitelist.

rule_config shape (post-validation, normalized):

    {
      "direction_mode": "both" | "long_only" | "short_only",
      "long":  {"logic": "all" | "any", "conditions": [<cond>, ...]},
      "short": {"logic": "all" | "any", "conditions": [<cond>, ...]},
    }

where <cond> is ``{"left": <catalog key>, "op": "gt|gte|lt|lte", "right": <catalog
key or number>}``. ``left`` resolves to an indicator value; ``right`` resolves to
another indicator value (if a catalog key) or a numeric constant.
"""

from __future__ import annotations

import json
import logging

from django.conf import settings

logger = logging.getLogger("signals.strategy_builder")

MAX_CONDITIONS_PER_SIDE = 6

# Indicator keys a rule may reference, with plain-English labels for the LLM. Keys
# MUST match apps.signals.indicators.compute_indicators output (numeric fields
# only — fib_leg_dir is a string direction and is intentionally excluded).
INDICATOR_CATALOG: dict[str, str] = {
    "close": "current close price",
    "high_24h": "24h high",
    "low_24h": "24h low",
    "volume": "current candle volume",
    "volume_ma20": "20-period volume moving average",
    "ema9": "9-period EMA",
    "ema21": "21-period EMA",
    "ema50": "50-period EMA",
    "ema200": "200-period EMA",
    "rsi": "RSI (14), 0-100",
    "macd_line": "MACD line",
    "macd_signal": "MACD signal line",
    "macd_hist": "MACD histogram (positive = bullish)",
    "bb_upper": "Bollinger upper band",
    "bb_mid": "Bollinger middle band",
    "bb_lower": "Bollinger lower band",
    "atr": "ATR (14), volatility",
    "adx": "ADX (14), trend strength 0-100",
    "stoch_k": "Stochastic %K, 0-100",
    "stoch_d": "Stochastic %D, 0-100",
    "vwap": "session VWAP",
    "swing_high": "recent swing high",
    "swing_low": "recent swing low",
    "fib_retrace": "retracement into the last impulse leg, 0-1",
}

OPS = {
    "gt": lambda a, b: a > b,
    "gte": lambda a, b: a >= b,
    "lt": lambda a, b: a < b,
    "lte": lambda a, b: a <= b,
}
_OP_TEXT = {"gt": ">", "gte": "≥", "lt": "<", "lte": "≤"}

DIRECTION_MODES = ("both", "long_only", "short_only")


class StrategyBuildError(ValueError):
    """Raised when a user's description can't be turned into a valid rule."""


# --- LLM: natural language -> raw rule JSON ---------------------------------

_CONDITION_SCHEMA = {
    "type": "object",
    "properties": {
        "left": {"type": "string", "enum": list(INDICATOR_CATALOG)},
        "op": {"type": "string", "enum": list(OPS)},
        # A catalog key ("ema200") or a number as a string ("30"). Parsed + range-
        # checked in _normalize_condition — kept as a string so the schema stays
        # strict (no union types).
        "right": {"type": "string"},
    },
    "required": ["left", "op", "right"],
    "additionalProperties": False,
}

_SIDE_SCHEMA = {
    "type": "object",
    "properties": {
        "logic": {"type": "string", "enum": ["all", "any"]},
        "conditions": {"type": "array", "items": _CONDITION_SCHEMA},
    },
    "required": ["logic", "conditions"],
    "additionalProperties": False,
}

RULE_SCHEMA = {
    "type": "object",
    "properties": {
        # False when the sentence can't be expressed with these indicators (e.g.
        # "buy when Elon tweets") — surfaced to the user as a rephrase prompt.
        "understood": {"type": "boolean"},
        "name": {"type": "string"},
        "description": {"type": "string"},
        "summary": {"type": "string"},
        "direction_mode": {"type": "string", "enum": list(DIRECTION_MODES)},
        "long": _SIDE_SCHEMA,
        "short": _SIDE_SCHEMA,
    },
    "required": ["understood", "name", "description", "summary", "direction_mode", "long", "short"],
    "additionalProperties": False,
}

_SYSTEM_PROMPT = (
    "You convert a trader's plain-English strategy into a structured JSON rule for a "
    "technical-analysis signal engine. You may ONLY reference the provided indicator "
    "keys and the operators gt (>), gte (>=), lt (<), lte (<=). Each condition compares "
    "one indicator ('left') against either another indicator key or a numeric constant "
    "('right', always given as a string). Build the 'long' side (conditions for a BUY) "
    "and, unless the user asked for one direction only, the mirrored 'short' side (a "
    "SELL). Use 'logic':'all' when conditions must all hold (the usual case), 'any' only "
    "if the user clearly means OR. Set direction_mode to long_only/short_only if the user "
    "asked for only longs/shorts, else 'both'. Write a short name, a one-line description, "
    "and a plain-English 'summary' of exactly what you built so the user can confirm it. "
    "If the description cannot be expressed with these indicators, set understood=false and "
    "leave the sides empty. Respond strictly in the requested JSON schema; these are "
    "informational/algorithmic outputs, not financial advice."
)


def _catalog_help() -> str:
    return "\n".join(f"- {k}: {v}" for k, v in INDICATOR_CATALOG.items())


def build_rule_from_text(text: str) -> dict:
    """Turn a user's sentence into ``{name, description, summary, rule_config}``.

    Raises StrategyBuildError with a user-facing message if the text is empty, the
    LLM can't map it to the indicator catalog, or the produced rule is invalid.
    """
    text = (text or "").strip()
    if not text:
        raise StrategyBuildError("Describe your strategy in a sentence first.")
    if len(text) > 500:
        raise StrategyBuildError("Please keep the description under 500 characters.")

    from .engine import SignalEngineError, _client  # lazy: avoids import cycle

    user_prompt = (
        f"Available indicator keys:\n{_catalog_help()}\n\n"
        f"Trader's strategy description:\n\"{text}\"\n\n"
        "Produce the JSON rule."
    )
    try:
        resp = _client().chat.completions.create(
            model=settings.OPENAI_MODEL,
            temperature=0,
            max_tokens=900,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "strategy_rule", "strict": True, "schema": RULE_SCHEMA},
            },
        )
    except SignalEngineError:
        raise
    except Exception as exc:  # network / API error
        logger.warning("strategy build LLM call failed: %s", exc)
        raise StrategyBuildError("Couldn't reach the strategy builder right now. Try again.")

    content = (resp.choices[0].message.content or "").strip()
    try:
        raw = json.loads(content)
    except json.JSONDecodeError:
        raise StrategyBuildError("Couldn't interpret that strategy. Try rephrasing it.")

    if not raw.get("understood", False):
        raise StrategyBuildError(
            "I couldn't express that with the available indicators. Try describing it in "
            "terms of price, EMAs, RSI, MACD, Bollinger Bands, ADX, volume or VWAP."
        )

    rule_config = validate_rule_config(
        {
            "direction_mode": raw.get("direction_mode", "both"),
            "long": raw.get("long") or {"logic": "all", "conditions": []},
            "short": raw.get("short") or {"logic": "all", "conditions": []},
        }
    )
    name = (raw.get("name") or "Custom strategy").strip()[:80]
    description = (raw.get("description") or "").strip()[:240]
    summary = (raw.get("summary") or rule_summary(rule_config)).strip()[:500]
    return {"name": name, "description": description, "summary": summary, "rule_config": rule_config}


# --- validation / normalization ---------------------------------------------

def _normalize_condition(cond: dict) -> dict:
    if not isinstance(cond, dict):
        raise StrategyBuildError("A rule condition was malformed.")
    left, op, right = cond.get("left"), cond.get("op"), cond.get("right")
    if left not in INDICATOR_CATALOG:
        raise StrategyBuildError(f"Unknown indicator '{left}'.")
    if op not in OPS:
        raise StrategyBuildError(f"Unsupported operator '{op}'.")
    # right is a catalog key or a numeric constant (LLM sends it as a string).
    if isinstance(right, str) and right in INDICATOR_CATALOG:
        norm_right: object = right
    else:
        try:
            norm_right = float(right)
        except (TypeError, ValueError):
            raise StrategyBuildError(f"'{right}' isn't an indicator or a number.")
    return {"left": left, "op": op, "right": norm_right}


def _normalize_side(side: dict) -> dict:
    if not isinstance(side, dict):
        side = {}
    logic = side.get("logic", "all")
    if logic not in ("all", "any"):
        logic = "all"
    conds = side.get("conditions") or []
    if len(conds) > MAX_CONDITIONS_PER_SIDE:
        raise StrategyBuildError(f"Use at most {MAX_CONDITIONS_PER_SIDE} conditions per side.")
    return {"logic": logic, "conditions": [_normalize_condition(c) for c in conds]}


def validate_rule_config(cfg: dict) -> dict:
    """Return a normalized rule_config, or raise StrategyBuildError. Injection-safe:
    every key/operator is checked against the whitelist and the rule only ever
    performs numeric comparisons over known indicator values."""
    if not isinstance(cfg, dict):
        raise StrategyBuildError("Invalid strategy rule.")
    mode = cfg.get("direction_mode", "both")
    if mode not in DIRECTION_MODES:
        raise StrategyBuildError("Invalid direction mode.")
    long = _normalize_side(cfg.get("long") or {})
    short = _normalize_side(cfg.get("short") or {})
    # Require at least one condition on each side the mode actually uses.
    if mode in ("both", "long_only") and not long["conditions"]:
        raise StrategyBuildError("The strategy needs at least one BUY condition.")
    if mode in ("both", "short_only") and not short["conditions"]:
        raise StrategyBuildError("The strategy needs at least one SELL condition.")
    return {"direction_mode": mode, "long": long, "short": short}


# --- evaluation --------------------------------------------------------------

def _cond_holds(cond: dict, ind: dict) -> bool:
    left = ind.get(cond["left"])
    if left is None:
        return False  # fail-closed on a missing indicator
    right = cond["right"]
    if isinstance(right, str):
        rv = ind.get(right)
        if rv is None:
            return False
    else:
        rv = right
    try:
        return OPS[cond["op"]](float(left), float(rv))
    except (TypeError, ValueError):
        return False


def _side_holds(side: dict, ind: dict) -> bool:
    conds = side.get("conditions") or []
    if not conds:
        return False
    results = (_cond_holds(c, ind) for c in conds)
    return all(results) if side.get("logic", "all") == "all" else any(results)


def evaluate_rule_config(cfg: dict, indicators: dict) -> str | None:
    """Direction implied by a custom rule for this indicator snapshot: 'BUY',
    'SELL', or None. Returns None if both sides fire (contradiction) or neither."""
    if not isinstance(cfg, dict):
        return None
    mode = cfg.get("direction_mode", "both")
    long_ok = mode in ("both", "long_only") and _side_holds(cfg.get("long") or {}, indicators)
    short_ok = mode in ("both", "short_only") and _side_holds(cfg.get("short") or {}, indicators)
    if long_ok and short_ok:
        return None
    if long_ok:
        return "BUY"
    if short_ok:
        return "SELL"
    return None


# --- human-readable output ---------------------------------------------------

def _fmt(v) -> str:
    """Magnitude-aware number formatting (mirrors engine._p, kept local to avoid an
    import cycle)."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "n/a"
    if v == int(v):
        return f"{int(v):,}"
    a = abs(v)
    return f"{v:,.2f}" if a >= 100 else (f"{v:.4f}" if a >= 1 else f"{v:.5f}")


def _cond_phrase(cond: dict, ind: dict | None) -> str:
    left_lbl = cond["left"].upper() if cond["left"].startswith(("ema", "rsi", "adx")) else cond["left"]
    op = _OP_TEXT[cond["op"]]
    right = cond["right"]
    if isinstance(right, str):
        right_lbl = right.upper() if right.startswith(("ema", "rsi", "adx")) else right
        rhs = f"{right_lbl}"
        if ind is not None and ind.get(right) is not None:
            rhs += f" ({_fmt(ind[right])})"
    else:
        rhs = _fmt(right)
    lhs = left_lbl
    if ind is not None and ind.get(cond["left"]) is not None:
        lhs += f" {_fmt(ind[cond['left']])}"
    return f"{lhs} {op} {rhs}"


def rule_summary(cfg: dict) -> str:
    """Static plain-English summary of a rule (no live values) for cards/UI."""
    parts = []
    mode = cfg.get("direction_mode", "both")
    if mode in ("both", "long_only") and cfg.get("long", {}).get("conditions"):
        joiner = " and " if cfg["long"].get("logic", "all") == "all" else " or "
        parts.append("BUY when " + joiner.join(_cond_phrase(c, None) for c in cfg["long"]["conditions"]))
    if mode in ("both", "short_only") and cfg.get("short", {}).get("conditions"):
        joiner = " and " if cfg["short"].get("logic", "all") == "all" else " or "
        parts.append("SELL when " + joiner.join(_cond_phrase(c, None) for c in cfg["short"]["conditions"]))
    return "; ".join(parts) or "No conditions."


def describe_match(service_name: str, cfg: dict, indicators: dict, direction: str) -> tuple[str, str]:
    """Reasoning + invalidation text for a custom-strategy signal card — the
    conditions that fired, with their live values."""
    side = cfg.get("long" if direction == "BUY" else "short", {})
    held = [c for c in side.get("conditions", []) if _cond_holds(c, indicators)]
    body = "; ".join(_cond_phrase(c, indicators) for c in held) or "your conditions aligned"
    reasoning = f"{service_name} {direction} — {body}."
    invalidation = "The setup is invalidated if these conditions no longer hold or the stop is hit."
    return reasoning, invalidation
