"""Strategy versions — which exact rules produced a signal (newPRD §3 P2, §4).

The engine is tuned live: dozens of SIGNAL_* settings, module gates seeded at
startup, and the rule code itself all change what gets called and how it's managed.
Without a version on each Signal, the record silently blends every configuration
the product has ever run, and a result can't be attributed to — or reproduced from —
the rules that made it.

A version is a frozen snapshot of everything that decides a signal, fingerprinted:

* ``engine``   — the rule settings (the signal_config list, minus business/ops knobs,
                 plus stop geometry) and the resolved pregate module state.
* ``code``     — a hash of the rule code's AST, so editing a comment or docstring
                 does NOT mint a version but changing a threshold in code does.
* ``strategy`` — the service's own definition (slug, kind, custom rule_config).
* ``roster``   — the active built-in strategies; confluence is scored across them,
                 so switching one on or off changes what every strategy delivers.

Same fingerprint => same version, forever. Any change => the next number for that
strategy, automatically: nothing has to remember to bump it.
"""

from __future__ import annotations

import ast
import hashlib
import json
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Max

# (env var name, settings attribute) — the engine-tuning block. Shared with
# `manage.py signal_config` so "what the process is running" and "what a version
# records" can never be two different lists.
ENGINE_SETTINGS = [
    ("SIGNAL_ENGINE_ENABLED", "SIGNAL_ENGINE_ENABLED"),
    ("SIGNAL_SHADOW_MODE", "SIGNAL_SHADOW_MODE"),
    ("SIGNAL_ENGINE_MODE", "SIGNAL_ENGINE_MODE"),
    ("SIGNAL_PREGATE_ENABLED", "SIGNAL_PREGATE_ENABLED"),
    ("SIGNAL_MIN_CONFIDENCE", "SIGNAL_MIN_CONFIDENCE"),
    ("SIGNAL_MIN_CONFIDENCE_REVERSION", "SIGNAL_MIN_CONFIDENCE_REVERSION"),
    ("SIGNAL_MIN_CONFIDENCE_BY_STRATEGY", "SIGNAL_MIN_CONFIDENCE_BY_STRATEGY"),
    ("SIGNAL_FOREX_STRATEGIES", "SIGNAL_FOREX_STRATEGIES"),
    ("SIGNAL_MAX_PER_CURRENCY", "SIGNAL_MAX_PER_CURRENCY"),
    ("SIGNAL_MAX_CRYPTO_PER_DIRECTION", "SIGNAL_MAX_CRYPTO_PER_DIRECTION"),
    # Was missing from this list, so `signal_config` reported nothing for the single
    # highest-impact gate measured on crypto (fades opposing BTC's trend, +2.6 points
    # on Bollinger Fade) — the exact "is it actually on in production?" question this
    # command exists to answer.
    ("SIGNAL_LEADER_GATE", "SIGNAL_LEADER_GATE"),
    ("SIGNAL_LOSS_BREAKER", "SIGNAL_LOSS_BREAKER"),
    ("SIGNAL_SUPPRESS_PROGRESSED", "SIGNAL_SUPPRESS_PROGRESSED"),
    ("SIGNAL_MAX_DELIVERY_AGE_BARS", "SIGNAL_MAX_DELIVERY_AGE_BARS"),
    ("SIGNAL_MAX_DELIVERY_AGE_BARS_FEED", "SIGNAL_MAX_DELIVERY_AGE_BARS_FEED"),
    ("SIGNAL_MAX_ENTRY_DRIFT", "SIGNAL_MAX_ENTRY_DRIFT"),
    ("SIGNAL_SHADOW_ASSET_CLASSES", "SIGNAL_SHADOW_ASSET_CLASSES"),
    ("SIGNAL_EVAL_BARS_BY_ASSET", "SIGNAL_EVAL_BARS_BY_ASSET"),
    ("SIGNAL_FOREX_SKIP_HOURS_UTC", "SIGNAL_FOREX_SKIP_HOURS_UTC"),
    ("SIGNAL_TIMEFRAMES", "SIGNAL_TIMEFRAMES"),
    ("SIGNAL_CONFLUENCE_MIN", "SIGNAL_CONFLUENCE_MIN"),
    ("SIGNAL_CONFLUENCE_MIN_REVERSION", "SIGNAL_CONFLUENCE_MIN_REVERSION"),
    ("SIGNAL_REGIME_FILTER_ENABLED", "SIGNAL_REGIME_FILTER_ENABLED"),
    ("SIGNAL_ADX_MIN", "SIGNAL_ADX_MIN"),
    ("SIGNAL_ADX_MAX_REVERSION", "SIGNAL_ADX_MAX_REVERSION"),
    ("SIGNAL_EMA_SEP_MIN_ATR", "SIGNAL_EMA_SEP_MIN_ATR"),
    ("SIGNAL_EMA_GATE", "SIGNAL_EMA_GATE"),
    ("SIGNAL_EMA200_TREND_FILTER", "SIGNAL_EMA200_TREND_FILTER"),
    ("SIGNAL_STRUCTURE_TREND_FILTER", "SIGNAL_STRUCTURE_TREND_FILTER"),
    ("SIGNAL_HTF_REGIME_ENABLED", "SIGNAL_HTF_REGIME_ENABLED"),
    ("SIGNAL_RSI_OVERBOUGHT", "SIGNAL_RSI_OVERBOUGHT"),
    ("SIGNAL_RSI_OVERSOLD", "SIGNAL_RSI_OVERSOLD"),
    ("SIGNAL_OVEREXT_ATR_MULT", "SIGNAL_OVEREXT_ATR_MULT"),
    ("SIGNAL_REENTRY_COOLDOWN_BARS", "SIGNAL_REENTRY_COOLDOWN_BARS"),
    ("SIGNAL_EXIT_ON_TREND_BREAK", "SIGNAL_EXIT_ON_TREND_BREAK"),
    ("SIGNAL_HTF_STRUCTURE_ENABLED", "SIGNAL_HTF_STRUCTURE_ENABLED"),
    ("SIGNAL_EVAL_BARS", "SIGNAL_EVAL_BARS"),
    ("SIGNAL_EXIT_MODEL", "SIGNAL_EXIT_MODEL"),
    ("SIGNAL_FIB_PULLBACK_MIN", "SIGNAL_FIB_PULLBACK_MIN"),
    ("SIGNAL_FIB_PULLBACK_MAX", "SIGNAL_FIB_PULLBACK_MAX"),
    ("SIGNAL_SKIP_CRYPTO_WEEKEND", "SIGNAL_SKIP_CRYPTO_WEEKEND"),
    ("SIGNAL_SCAN_SYMBOL_LIMIT", "SIGNAL_SCAN_SYMBOL_LIMIT"),
    ("SIGNAL_UNIVERSE_TOP_N", "SIGNAL_UNIVERSE_TOP_N"),
    ("SIGNAL_UNIVERSE_MIN_HISTORY_DAYS", "SIGNAL_UNIVERSE_MIN_HISTORY_DAYS"),
    ("SIGNAL_FREE_TRIAL_DAYS", "SIGNAL_FREE_TRIAL_DAYS"),
    ("SIGNAL_UPGRADE_NUDGE_DAYS", "SIGNAL_UPGRADE_NUDGE_DAYS"),
]

# In the list above for `signal_config`'s sake, but they don't change what a strategy
# calls or how a trade is managed — on/off switches, capacity, and plan marketing.
# Versioning on them would split one strategy's record every time a trial changed.
NOT_RULES = frozenset({
    "SIGNAL_ENGINE_ENABLED",
    "SIGNAL_SHADOW_MODE",
    "SIGNAL_SCAN_SYMBOL_LIMIT",
    "SIGNAL_FREE_TRIAL_DAYS",
    "SIGNAL_UPGRADE_NUDGE_DAYS",
})

# Rule settings `signal_config` prints in its own format (or not at all) but that
# decide where the stop — and therefore every TP — lands, or which setups pass.
EXTRA_RULE_SETTINGS = (
    "SIGNAL_RULE_CONFIDENCE",
    "SIGNAL_MIN_CONFIDENCE_FOREX",
    "SIGNAL_ADX_MIN_BY_WEEKDAY",
    "SIGNAL_REVERSION_HTF_GUARD",
    "SIGNAL_ATR_STOP_FLOOR",
    "SIGNAL_ATR_STOP_CAP",
    "SIGNAL_ATR_FLOOR_REVERSION",
    "SIGNAL_ATR_CAP_REVERSION",
)

RULE_SETTINGS = tuple(
    attr for _, attr in ENGINE_SETTINGS if attr not in NOT_RULES
) + EXTRA_RULE_SETTINGS

_APP_DIR = Path(__file__).resolve().parent

# Whole modules that hold strategy rules, levels, outcome grading and delivery gates.
RULE_MODULES = (
    "pregate.py", "levels.py", "indicators.py", "engine.py", "prompt.py",
    "confluence.py", "evaluate.py", "breaker.py", "strategy_builder.py",
)

# tasks.py mixes the scan's gates with Telegram/web-push formatting. Only the scan
# and evaluation logic is rules — hashing the whole file would mint a new version
# every time a push message's wording changed.
TASKS_RULE_NAMES = frozenset({
    "MIN_CANDLES", "_HTF_MAP", "INTERVAL_SECONDS",
    "_closed_candles", "_same_direction_repeat_ready", "_scan_direction_allows",
    "_invalidate_opposite_pending", "_htf_direction", "_htf_structure",
    "_htf_structure_ok", "_adx_min_now", "_regime_ok", "leader_trend", "run_scan",
    "_invalidate_trend_breaks", "_eval_bars_for", "run_evaluation",
})


def _strip_docstrings(tree: ast.AST) -> ast.AST:
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                node.body = body[1:] or [ast.Pass()]
    return tree


def _top_level_name(node: ast.stmt) -> str | None:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return node.name
    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        target = node.targets[0]
        if isinstance(target, ast.Name):
            return target.id
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return node.target.id
    return None


@lru_cache(maxsize=1)
def code_hash() -> str:
    """Hash of the rule code's AST. Comments and docstrings don't count; logic does.

    Cached for the process: code can't change under a running worker, and a deploy
    restarts it.
    """
    h = hashlib.sha256()
    for name in RULE_MODULES:
        tree = _strip_docstrings(ast.parse((_APP_DIR / name).read_text(encoding="utf-8")))
        h.update(name.encode())
        h.update(ast.dump(tree, include_attributes=False).encode())

    tasks_tree = _strip_docstrings(
        ast.parse((_APP_DIR / "tasks.py").read_text(encoding="utf-8"))
    )
    picked = sorted(
        (_top_level_name(node), ast.dump(node, include_attributes=False))
        for node in tasks_tree.body
        if _top_level_name(node) in TASKS_RULE_NAMES
    )
    for name, dumped in picked:
        h.update(f"tasks.{name}".encode())
        h.update(dumped.encode())
    return h.hexdigest()


def _jsonable(value):
    """Settings values in a stable JSON shape (sets have no order; tuples are lists)."""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (set, frozenset)):
        return sorted(_jsonable(v) for v in value)
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def engine_snapshot() -> dict:
    """The global part of every strategy's version: rule settings + resolved gates."""
    from . import pregate

    return {
        "settings": {attr: _jsonable(getattr(settings, attr, None)) for attr in RULE_SETTINGS},
        # Seeded from env in SignalsConfig.ready(); recorded as RESOLVED, since that is
        # what the engine actually read.
        "pregate": {
            "EMA_GATE_MODE": _jsonable(pregate.EMA_GATE_MODE),
            "EMA200_TREND_FILTER": _jsonable(pregate.EMA200_TREND_FILTER),
            "STRUCTURE_TREND_FILTER": _jsonable(pregate.STRUCTURE_TREND_FILTER),
            "OVEREXT_ATR_MULT": _jsonable(pregate.OVEREXT_ATR_MULT),
            "RSI_OVERBOUGHT": _jsonable(pregate.RSI_OVERBOUGHT),
            "RSI_OVERSOLD": _jsonable(pregate.RSI_OVERSOLD),
        },
    }


def active_roster() -> list[str]:
    from .models import SignalService

    return sorted(
        SignalService.objects.filter(is_active=True, owner__isnull=True)
        .values_list("slug", flat=True)
    )


def snapshot_for(service, engine: dict | None = None, roster: list[str] | None = None) -> dict:
    """Everything that decides what ``service`` calls and how its trades are managed."""
    from .pregate import kind_of

    return {
        "engine": engine if engine is not None else engine_snapshot(),
        "code": code_hash(),
        "roster": roster if roster is not None else active_roster(),
        "strategy": {
            "slug": service.slug,
            "kind": kind_of(service.slug),
            "custom": service.is_custom,
            "rule_config": _jsonable(service.rule_config),
        },
    }


def fingerprint(snapshot: dict) -> str:
    return hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def version_for(service, engine: dict | None = None, roster: list[str] | None = None):
    """The StrategyVersion for ``service`` under the rules in force right now.

    Reuses the existing row when nothing changed; otherwise records the next number.
    Safe under concurrent scans: the (service, fingerprint) and (service, number)
    constraints decide any race, and the loser re-reads the winner's row.
    """
    from .models import StrategyVersion

    snap = snapshot_for(service, engine=engine, roster=roster)
    fp = fingerprint(snap)

    existing = StrategyVersion.objects.filter(service=service, fingerprint=fp).first()
    if existing:
        return existing

    for _ in range(3):
        try:
            with transaction.atomic():
                last = (
                    StrategyVersion.objects.filter(service=service)
                    .aggregate(n=Max("number"))["n"] or 0
                )
                return StrategyVersion.objects.create(
                    service=service, number=last + 1, fingerprint=fp,
                    snapshot=snap, code_hash=snap["code"],
                )
        except IntegrityError:
            existing = StrategyVersion.objects.filter(service=service, fingerprint=fp).first()
            if existing:
                return existing
    raise RuntimeError(f"could not record a strategy version for {service.slug}")
