"""Research-only derivation of contract probability RANGES from the repository's historical outcome distributions. PURE.

*** WHAT THIS IS, AND WHAT IT IS NOT ***
It turns the packets' own reference class (past games whose pre-kickoff baseline resembled this one, leak-free) into a
frequency and an uncertainty range for each listed side. Every number is a ``HISTORICAL_ESTIMATE``: descriptive,
uncalibrated, never a validated probability and never an approval. It changes no prediction model, places no
order, sizes
no stake and never labels anything a candidate (``EVALUATED_CANDIDATE`` is reserved for an analyst).

Honest statuses, one per priceable side:

* ``DERIVED_ESTIMATE``      a reference class of >= MIN_COMPARABLES games exists AND the (quantity, period) it rests on
                            showed out-of-sample skill in the walk-forward backtest below. A range and a fee-inclusive
                            Bet Up To (from the LOWER bound) are produced.
* ``SHAPE_ONLY_REFERENCE``  the reference class exists but its (quantity, period) has shown no demonstrated skill (or
                            has not been tested). The range is shown as SHAPE, the Bet Up To is withheld: only an
                            independent analyst handicap can establish one. Nothing is excluded for this reason.
* ``INSUFFICIENT_EVIDENCE`` no usable reference class (no football history, reference class too thin) or the repository
                            holds no data for the quantity (first-TD team, stat props). Named missing evidence.

The range is the UNION of (a) the Wilson interval for the reference class's sampling error and (b) the frequency's
sensitivity to moving each team's projected points by the baseline's own stated uncertainty times a matchup-specific
widening multiplier (QB stability, QB confirmation, listed absences). Widening can only LOWER a lower bound, so a
missing QB or injury feed makes a side harder to bet, not impossible to evaluate. The multiplier constants are an
explicit,
UNCALIBRATED policy (``POLICY``): nothing in the repository measures how much a QB change moves a game.
"""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from cfb_edge_finder.research_packet import history as hist

SCHEMA = "cfb_derived_estimates/1.0.0"
SKILL_SCHEMA = "cfb_derived_skill/1.0.0"

DERIVED_ESTIMATE = "DERIVED_ESTIMATE"
SHAPE_ONLY_REFERENCE = "SHAPE_ONLY_REFERENCE"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
SOURCE = "DERIVED_HISTORICAL"

SUPPORTED = "SUPPORTED"
SHAPE_ONLY = "SHAPE_ONLY"

WILSON_Z = 1.96
CLIP = (0.005, 0.995)

#: Explicit, owner-adjustable, UNCALIBRATED widening policy. Multiplies the baseline's own stated uncertainty (points).
POLICY: dict[str, Any] = {
    "calibrated": False,
    "applies_to": "each team's projected points, in both directions; the range is the union over the four corners",
    "base_multiplier": 1.0,
    "qb_unconfirmed_add": 0.25,
    "qb_unstable_add": 0.5,  # a second passer took >= 25% of the last four games' attempts
    "qb_changed_add": 0.5,  # changed in the last 3 games, or the leader differs from the last game
    "absences_add": 0.5,  # the injury listing names entries for the team, or flags the QB uncertain
    "cap": 2.5,
    "note": (
        "widening only: a larger multiplier can lower a lower bound and never raises one. No source measures how much "
        "a QB change or an absence moves a game, so these are stated assumptions, not estimates."
    ),
}

PERIOD_KEY = {"full_game": "game", "h1": "h1", "h2": "h2", "q1": "q1", "q2": "q2", "q3": "q3", "q4": "q4"}
QUANTITY_OF_KIND = {"win": "margin", "margin_over": "margin", "total_over": "total", "team_points_over": "team"}


# ------------------------------------------------------------------------------------------------ contract meaning


def contract_quantity(c: dict[str, Any]) -> dict[str, Any] | None:
    """The structured YES outcome of a contract from its own fields (football home/away oriented), or None."""
    kind = c.get("kind")
    period = PERIOD_KEY.get(c.get("period"))
    if period is None:
        return None
    if kind == "win":
        if c.get("outcome") == "tie":
            return {"type": "win", "period": period, "outcome": "tie"}
        return {"type": "win", "period": period, "side": c.get("side")} if c.get("side") else None
    if kind == "margin_over" and c.get("side") and c.get("line") is not None:
        return {"type": "margin_over", "period": period, "side": c["side"], "threshold": c["line"]}
    if kind == "total_over" and c.get("line") is not None:
        return {"type": "total_over", "period": period, "threshold": c["line"]}
    if kind == "team_points_over" and c.get("side") and c.get("line") is not None:
        return {"type": "team_points_over", "period": period, "side": c["side"], "threshold": c["line"]}
    return None


def skill_key(qty: dict[str, Any]) -> str:
    return f"{QUANTITY_OF_KIND[qty['type']]}|{qty['period']}"


_WINMARGIN = re.compile(r"^(?P<code>[A-Z0-9]+?)(?:(?P<lo>\d+)TO(?P<hi>\d+)|(?P<plus>\d+)PLUS)$")


def _special(c: dict[str, Any], codes: dict[str, str]) -> dict[str, Any] | None:
    """Families the generic quantity map does not cover but the history CAN answer: winning-margin buckets, overtime,
    and the 1H-result / game-result joint. Anything unparseable returns None (and becomes INSUFFICIENT_EVIDENCE)."""
    fam = c.get("family")
    suffix = str(c.get("ticker", "")).rsplit("-", 1)[-1]
    side_of = {code: s for s, code in codes.items() if code}
    if fam == "winning_margin":
        m = _WINMARGIN.match(suffix)
        if not m or m["code"] not in side_of:
            return None
        lo, hi = (int(m["lo"]), int(m["hi"])) if m["lo"] else (int(m["plus"]), None)
        return {"special": "winning_margin", "side": side_of[m["code"]], "lo": lo, "hi": hi, "skill": "margin|game"}
    if fam == "overtime":
        return {"special": "overtime", "skill": None}
    if fam == "unknown" and str(c.get("ticker", "")).startswith("KXNCAAF1HFT"):
        for h1code in [*side_of, "TIE"]:
            if suffix.startswith(h1code) and (rest := suffix[len(h1code) :]) in side_of:
                return {
                    "special": "h1_game_joint",
                    "h1": "tie" if h1code == "TIE" else side_of[h1code],
                    "game": side_of[rest],
                    "skill": None,
                }
        return None
    return None


def _special_frequency(chosen: list[dict[str, Any]], q: dict[str, Any]) -> tuple[int, int] | None:
    hits = n = 0
    for g in chosen:
        if q["special"] == "overtime":
            view = hist.period_view(g)
            if view is None:
                continue
            n += 1
            hits += 1 if view["ot"] else 0
        elif q["special"] == "winning_margin":
            margin = g["actual_home"] - g["actual_away"]
            m = margin if q["side"] == "home" else -margin
            n += 1
            hits += 1 if (m >= q["lo"] and (q["hi"] is None or m <= q["hi"]) and m > 0) else 0
        elif q["special"] == "h1_game_joint":
            view = hist.period_view(g)
            if view is None:
                continue
            h1 = view["h1"][0] - view["h1"][1]
            h1_side = "tie" if h1 == 0 else ("home" if h1 > 0 else "away")
            gm = g["actual_home"] - g["actual_away"]
            game_side = "home" if gm > 0 else "away" if gm < 0 else "tie"
            n += 1
            hits += 1 if (h1_side == q["h1"] and game_side == q["game"]) else 0
    return (hits, n) if n else None


# ------------------------------------------------------------------------------------------------ frequencies


def wilson(hits: int, n: int, z: float = WILSON_Z) -> tuple[float, float]:
    if n <= 0:
        return 0.0, 1.0
    p = hits / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, centre - half), min(1.0, centre + half)


def _hits_n(chosen: list[dict[str, Any]], qty: dict[str, Any]) -> tuple[int, int] | None:
    if qty.get("special"):
        return _special_frequency(chosen, qty)
    vals = hist.quantity_values(chosen, qty)
    if not vals:
        return None
    freq = hist.frequency_for(chosen, qty)
    if freq is None:
        return None
    n = freq["n"]
    return round(freq["yes_frequency"] * n), n


def league_sd(history_games: list[dict[str, Any]]) -> dict[str, float]:
    """League standard deviation of each (quantity|period), the unit the skill test's line range is measured in."""
    acc: dict[str, list[float]] = defaultdict(list)
    for g in history_games:
        for period in ("game", "h1", "h2", "q1", "q2", "q3", "q4"):
            if period == "game":
                p = (g["actual_home"], g["actual_away"])
            else:
                v = hist.period_view(g)
                p = None if v is None else v.get(period)
            if p is None:
                continue
            acc[f"margin|{period}"].append(p[0] - p[1])
            acc[f"total|{period}"].append(p[0] + p[1])
            acc[f"team|{period}"].extend([p[0], p[1]])
    out = {}
    for k, xs in acc.items():
        m = sum(xs) / len(xs)
        out[k] = math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs))
    return out


TESTED_RANGE_SD = 0.5  # the skill test placed lines within +/- this many league SDs of the reference-class median
TESTED_RANGE_PAD = 1.0  # points of slack for half-point line rounding


def in_tested_range(chosen: list[dict[str, Any]], q: dict[str, Any], sd: dict[str, float]) -> bool:
    """Is this contract's line inside the span the walk-forward skill test covered? Tails are extrapolation."""
    if q.get("special") or q.get("outcome") == "tie":
        return False
    vals = hist.quantity_values(chosen, q)
    key = skill_key(q)
    if not vals or key not in sd:
        return False
    med = sorted(vals)[len(vals) // 2]
    t = 0.0 if q["type"] == "win" else float(q["threshold"])
    if q["type"] == "margin_over" and q.get("side") == "away":
        t = -t  # the values are home-oriented: away margin > t  <=>  home margin < -t
    return abs(t - med) <= TESTED_RANGE_SD * sd[key] + TESTED_RANGE_PAD


def _clip(p: float) -> float:
    return min(CLIP[1], max(CLIP[0], p))


# ------------------------------------------------------------------------------------------------ matchup uncertainty


def qb_assessment(packet: dict[str, Any], team: str) -> dict[str, Any]:
    """Matchup-specific uncertainty for one team from the packet's own QB log and injury listing. Widens, never
    rejects."""
    qb = (packet.get("qb") or {}).get(team) or {}
    inj = ((packet.get("availability") or {}).get("espn_core_injury_listing") or {}).get(team) or {}
    reasons: list[str] = []
    mult = POLICY["base_multiplier"]
    if not qb:
        mult += POLICY["qb_unconfirmed_add"] + POLICY["qb_unstable_add"]
        reasons.append("NO_QB_BLOCK_IN_PACKET")
    else:
        if not qb.get("confirmed_starter"):
            mult += POLICY["qb_unconfirmed_add"]
            reasons.append("QB_NOT_CONFIRMED")
        log = (qb.get("game_log") or [])[-4:]
        total = sum(float(g.get("attempts") or 0) for g in log)
        proj = qb.get("projected_starter")
        if total > 0 and proj:
            share = sum(float(g.get("attempts") or 0) for g in log if g.get("passer") == proj) / total
            if share < 0.75:
                mult += POLICY["qb_unstable_add"]
                reasons.append(f"PROJECTED_STARTER_TOOK_{share:.0%}_OF_LAST_{len(log)}_GAMES_ATTEMPTS")
        elif proj is None:
            mult += POLICY["qb_unstable_add"]
            reasons.append("NO_PROJECTED_STARTER")
        if qb.get("changed_in_last_3_games") or qb.get("leader_differs_from_last_game"):
            mult += POLICY["qb_changed_add"]
            reasons.append("QB_CHANGED_RECENTLY")
    if inj.get("named_entries") or inj.get("qb_flagged_uncertain"):
        mult += POLICY["absences_add"]
        reasons.append("LISTED_ABSENCES" if not inj.get("qb_flagged_uncertain") else "QB_FLAGGED_UNCERTAIN")
    if inj.get("status") != "OBSERVED":
        reasons.append("INJURY_LISTING_NOT_OBSERVED")  # a gap, stated; an empty list is not health
    mult = min(mult, POLICY["cap"])
    level = "LOW" if mult <= 1.25 else "ELEVATED" if mult <= 1.75 else "HIGH"
    return {"multiplier": round(mult, 3), "level": level, "reasons": reasons}


# ------------------------------------------------------------------------------------------------ generation


def _corners(hp: float, ap: float, uh: float, ua: float) -> list[tuple[float, float]]:
    out = []
    for dh in (-uh, uh):
        for da in (-ua, ua):
            out.append(((hp + dh) - (ap + da), (hp + dh) + (ap + da)))
    return out


def derive_game(
    game_row: dict[str, Any],
    contracts: list[dict[str, Any]],
    packet: dict[str, Any] | None,
    history_games: list[dict[str, Any]],
    skill: dict[str, Any],
    side_codes: dict[str, str] | None = None,
    sd: dict[str, float] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """One entry per (contract, YES|NO) of a game, plus the game's assessment block. Pure."""
    key = game_row["game_key"]
    entries: list[dict[str, Any]] = []
    codes = side_codes or {}
    info: dict[str, Any] = {"game_key": key, "reference_class": None, "qb_assessment": None}

    base = (packet or {}).get("football", {}).get("baseline") if packet else None
    chosen: list[dict[str, Any]] = []
    window = None
    corner_sets: list[list[dict[str, Any]]] = []
    if base and base.get("home_points") is not None and game_row.get("football_history") == "PRESENT":
        cutoff = ((packet or {}).get("inputs") or {}).get("football_data_cutoff")
        pool = hist.eligible_reference(history_games, cutoff, None)
        chosen, window = hist.reference_class(pool, base["home_margin"], base["total_points"])
        qh, qa = qb_assessment(packet or {}, "home"), qb_assessment(packet or {}, "away")
        u = float(base.get("uncertainty_points") or 0.0)
        info["qb_assessment"] = {"home": qh, "away": qa, "baseline_uncertainty_points": u}
        for m_pred, t_pred in _corners(
            base["home_points"], base["away_points"], u * qh["multiplier"], u * qa["multiplier"]
        ):
            ch, _ = hist.reference_class(pool, m_pred, t_pred)
            corner_sets.append(ch)
        info["reference_class"] = {"n": len(chosen), "window": window, "corner_ns": [len(s) for s in corner_sets]}
    usable = len(chosen) >= hist.MIN_COMPARABLES

    for c in contracts:
        qty = contract_quantity(c)
        special = None if qty else _special(c, codes)
        q = qty or special
        for side in ("YES", "NO"):
            e: dict[str, Any] = {"game_key": key, "ticker": c["ticker"], "side": side, "source": SOURCE}
            if q is None:
                e.update(
                    status=INSUFFICIENT_EVIDENCE,
                    reason=f"no historical outcome data for a {c.get('family')} contract in this repository",
                    missing_evidence=_missing_for(c.get("family")),
                )
            elif not usable:
                why = (
                    "football history is absent for this game (no box-score reference class can be built)"
                    if game_row.get("football_history") != "PRESENT"
                    else f"reference class has {len(chosen)} comparables, below the {hist.MIN_COMPARABLES} minimum"
                )
                e.update(
                    status=INSUFFICIENT_EVIDENCE,
                    reason=why,
                    missing_evidence=["comparable past games for this matchup's baseline"],
                )
            else:
                hn = _hits_n(chosen, q)
                if hn is None or hn[1] < hist.MIN_COMPARABLES:
                    e.update(
                        status=INSUFFICIENT_EVIDENCE,
                        reason="the reference class holds too few games with this period's scoring",
                        missing_evidence=["period linescores for comparable games"],
                    )
                else:
                    hits, n = hn
                    lo_w, hi_w = wilson(hits, n)
                    lows, highs = [lo_w], [hi_w]
                    for s in corner_sets:
                        cn = _hits_n(s, q) if len(s) >= hist.MIN_COMPARABLES else None
                        if cn:
                            lows.append(cn[0] / cn[1])
                            highs.append(cn[0] / cn[1])
                    yes_low, yes_high, yes_pt = min(lows + [hits / n]), max(highs + [hits / n]), hits / n
                    sk = q["skill"] if special else skill_key(q)
                    lab = ((skill.get("keys") or {}).get(sk) or {}).get("label") if sk else None
                    if side == "YES":
                        lo, pt, hi = yes_low, yes_pt, yes_high
                    else:
                        lo, pt, hi = 1 - yes_high, 1 - yes_pt, 1 - yes_low
                    supported = lab == SUPPORTED
                    outside = False
                    if supported and not (sd and in_tested_range(chosen, q, sd)):
                        supported, outside = False, True
                    e.update(
                        status=DERIVED_ESTIMATE if supported else SHAPE_ONLY_REFERENCE,
                        reason=(
                            f"historical reference class n={n} (window {window}); range = sampling (Wilson) union "
                            f"baseline-uncertainty sensitivity; {('skill ' + (lab or 'UNTESTED'))}"
                        ),
                        probability={
                            "low": round(_clip(lo), 4),
                            "point": round(_clip(pt), 4),
                            "high": round(_clip(hi), 4),
                        },
                        reference_n=n,
                        skill_label=lab or "UNTESTED",
                        thesis=None,
                    )
                    if outside:
                        e["skill_label"] = "SUPPORTED_BUT_OUTSIDE_TESTED_LINE_RANGE"
                        e["bet_up_to_withheld"] = (
                            "the line lies outside the span the skill test covered (a tail rung): extrapolation, so "
                            "only an analyst handicap sets a ceiling"
                        )
                    elif not supported:
                        e["bet_up_to_withheld"] = (
                            "SHAPE_ONLY: no demonstrated skill; only an analyst handicap sets a ceiling"
                        )
            entries.append(e)
    return entries, info


def _missing_for(family: str | None) -> list[str]:
    return {
        "touchdown_scorer": ["drive-order / first-touchdown data"],
        "team_stat_prop": ["team receiving/rushing touchdown counts by player or position"],
        "game_stat_prop": ["defensive/special-teams touchdown rates"],
        "unknown": ["contract meaning could not be mapped to a structured outcome"],
        "winning_margin": ["contract suffix could not be mapped to a margin bucket"],
    }.get(family or "", ["contract meaning could not be mapped to a structured outcome"])


# ------------------------------------------------------------------------------------------------ walk-forward skill

SKILL_LINES_SD = (-0.5, -0.25, 0.0, 0.25, 0.5)
MIN_TEST_GAMES = 150


def family_skill(
    history_games: list[dict[str, Any]], test_from: str = "2025-09-01", reps: int = 1000
) -> dict[str, Any]:
    """Walk-forward test of the reference-class estimator against league frequency, per (quantity, period).

    For each game from ``test_from`` the reference class is built ONLY from earlier games, exactly as the packets do.
    Lines are placed around the reference class's own median (so they cover the range Kalshi lists) at fractions of the
    league standard deviation. Skill = Brier(league frequency) - Brier(reference class), averaged per game; the interval
    is a game-clustered bootstrap. SUPPORTED requires the interval's lower bound above zero. It measures distribution
    quality against a no-information baseline; the repository has no market lines, so it is NOT an edge versus Kalshi.
    """
    import random

    rows = sorted(
        (g for g in history_games if g.get("pred_total") is not None and g.get("home_periods")),
        key=lambda g: g["kickoff_utc"],
    )
    views = {id(g): hist.period_view(g) for g in rows}

    def vals(g: dict[str, Any], period: str) -> tuple[float, float] | None:
        if period == "game":
            return g["actual_home"], g["actual_away"]
        v = views[id(g)]
        return None if v is None else v.get(period)

    periods = ("game", "h1", "h2", "q1", "q2", "q3", "q4")

    def qty_values(g: dict[str, Any], quantity: str, period: str) -> float | None:
        p = vals(g, period)
        if p is None:
            return None
        return {"margin": p[0] - p[1], "total": p[0] + p[1], "team_home": p[0], "team_away": p[1]}[quantity]

    per_game: dict[str, list[float]] = defaultdict(list)
    events: dict[str, int] = defaultdict(int)
    test = [g for g in rows if g["kickoff_utc"] >= test_from]
    for g in test:
        past = [r for r in rows if r["kickoff_utc"] < g["kickoff_utc"]]
        if len(past) < 300:
            continue
        chosen, _ = hist.reference_class(past, g["pred_home"] - g["pred_away"], g["pred_total"])
        if len(chosen) < hist.MIN_COMPARABLES:
            continue
        game_diffs: dict[str, list[float]] = defaultdict(list)
        for period in periods:
            for quantity in ("margin", "total", "team_home", "team_away"):
                ref = [v for r in chosen if (v := qty_values(r, quantity, period)) is not None]
                uni = [v for r in past if (v := qty_values(r, quantity, period)) is not None]
                y = qty_values(g, quantity, period)
                if len(ref) < hist.MIN_COMPARABLES or len(uni) < 300 or y is None:
                    continue
                med = sorted(ref)[len(ref) // 2]
                mean = sum(uni) / len(uni)
                sd = math.sqrt(sum((v - mean) ** 2 for v in uni) / len(uni)) or 1.0
                diffs = []
                for k in SKILL_LINES_SD:
                    line = math.floor(med + k * sd) + 0.5
                    ev = 1.0 if y > line else 0.0
                    p_ref = (sum(v > line for v in ref) + 0.5) / (len(ref) + 1)
                    p_uni = (sum(v > line for v in uni) + 0.5) / (len(uni) + 1)
                    diffs.append((p_uni - ev) ** 2 - (p_ref - ev) ** 2)
                    events[f"{'team' if quantity.startswith('team') else quantity}|{period}"] += 1
                label = "team" if quantity.startswith("team") else quantity
                game_diffs[f"{label}|{period}"].append(sum(diffs) / len(diffs))
        for k_, ds_ in game_diffs.items():  # one value per GAME (home and away team quantities averaged)
            per_game[k_].append(sum(ds_) / len(ds_))
    rnd = random.Random(7)
    keys: dict[str, Any] = {}
    for key, ds in sorted(per_game.items()):
        n = len(ds)
        if n < MIN_TEST_GAMES:
            keys[key] = {"label": SHAPE_ONLY, "n_games": n, "reason": "too few test games"}
            continue
        mean = sum(ds) / n
        boots = sorted(sum(rnd.choice(ds) for _ in range(n)) / n for _ in range(reps))
        lo, hi = boots[int(0.025 * reps)], boots[int(0.975 * reps) - 1]
        keys[key] = {
            "label": SUPPORTED if lo > 0 else SHAPE_ONLY,
            "n_games": n,
            "n_events": events[key],
            "brier_gain_vs_league_frequency": round(mean, 5),
            "ci95": [round(lo, 5), round(hi, 5)],
        }
    return {
        "schema_version": SKILL_SCHEMA,
        "test_from": test_from,
        "method": family_skill.__doc__.strip().split("\n\n")[0],
        "rule": "SUPPORTED iff the game-clustered 95% interval of the Brier gain over league frequency is above 0",
        "not_an_edge_claim": "no market lines exist in the repository; this measures distribution quality only",
        "keys": keys,
    }


# ------------------------------------------------------------------------------------------------ whole-slate driver


def generate(
    root: Path,
    inv: Any,
    history_games: list[dict[str, Any]],
    skill: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Derived entries for every contract of every game in ``inv`` plus the method context for the report."""
    entries: list[dict[str, Any]] = []
    games_info: list[dict[str, Any]] = []
    sd = league_sd(history_games)
    for g in inv.games:
        key = g["game_key"]
        pkt_rel = g.get("packet")
        packet = (
            json.loads((Path(root) / pkt_rel).read_text()) if pkt_rel and (Path(root) / pkt_rel).is_file() else None
        )
        got, info = derive_game(g, inv.contracts[key], packet, history_games, skill, inv.side_codes.get(key), sd)
        entries.extend(got)
        games_info.append(info)
    doc = {"schema_version": SCHEMA, "authority": "RESEARCH_ONLY", "entries": entries}
    context = {"skill": skill, "policy": POLICY, "games": games_info}
    return doc, context
