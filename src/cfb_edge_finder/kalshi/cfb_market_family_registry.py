"""Historical Kalshi CFB market-family registry -- Milestone B.5.

*** WHAT THIS IS ***
A compact, documented classification of which college-football market
families Kalshi has GENUINELY, EVIDENTIALLY been observed to offer, built
from real research (CFTC self-certification filings, Kalshi's own market
URLs/tickers, and reporting that quotes Kalshi's own contract templates --
see docs/KALSHI_CFB_MARKET_AUDIT.md for the full evidence trail, every
source URL, and confidence rationale for every entry below). This is NOT
a generic "what do sportsbooks usually offer" list -- the whole point of
this milestone is to replace that assumption with Kalshi-specific evidence
before Milestone C designs any probability model around it.

*** WHY THIS EXISTS SEPARATELY FROM schemas.common.MarketFamily ***
`schemas.common.MarketFamily` (Milestone A) is a generic, sportsbook-shaped
enum written before any Kalshi-specific research existed. It is still used
for coverage-ledger ticker classification and is NOT replaced by this
module. This registry is the audit layer that validates (and where the
evidence disagrees, corrects) those generic assumptions against real
Kalshi evidence, and adds families schemas.common.MarketFamily has no slot
for at all (season/futures markets: national champion, conference champion,
Heisman, AP poll rank, coach markets, FCS national champion, regular-season
win-total ladders). Reconciling the two enums into one, if that turns out
to be the right move, is Milestone C work -- this module does not touch
schemas.common.MarketFamily.

*** CONFIDENCE LEVELS ***
Every entry's `historical_confidence` is one of:
  CONFIRMED  -- direct, dated primary evidence (a CFTC filing quote, a real
                Kalshi ticker/URL, a directly-quoted Kalshi contract
                template, or a directly-quoted market description).
  PROBABLE   -- evidence exists but is indirect, generic-to-"football"
                rather than CFB-specific, inferred from a template's
                grammar rather than a concrete multi-rung example, or
                stated by secondary sources without a primary-source
                confirmation.
  UNVERIFIED -- searched for directly and no confirming (or denying)
                evidence was found. This is NOT the same as "confirmed
                absent" -- see docs/KALSHI_CFB_MARKET_AUDIT.md for the
                specific families (FBS-vs-FCS single-game listings,
                first-half spread, team totals) where absence itself is
                unverified, not established.

*** PRIORITY VALUES ***
  CORE_V1               -- Milestone C should be able to price this first.
                            Requires historical_confidence CONFIRMED and a
                            non-null required_probability_primitive
                            (enforced by validate_registry() below).
  LATER_GAME_MODEL       -- a real, evidenced single-game family, but
                            secondary to the CORE_V1 set (e.g. first-half
                            totals) -- worth building once CORE_V1 is
                            validated, not before.
  FUTURES_SEPARATE_ENGINE -- a confirmed or probable season/futures family
                            that needs season-simulation, polling, or
                            award-prediction machinery entirely distinct
                            from a single-game score-distribution engine.
  UNSUPPORTED_UNVERIFIED  -- do not build yet: either the evidence is too
                            weak, or (touchdown props) the family is
                            legally self-certified but reporting directly
                            states it is not actually being offered for
                            college players.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class MarketScope(StrEnum):
    GAME_LEVEL = "game_level"
    FUTURES = "futures"


class EvidenceConfidence(StrEnum):
    CONFIRMED = "confirmed"
    PROBABLE = "probable"
    UNVERIFIED = "unverified"


class MilestoneCPriority(StrEnum):
    CORE_V1 = "core_v1"
    LATER_GAME_MODEL = "later_game_model"
    FUTURES_SEPARATE_ENGINE = "futures_separate_engine"
    UNSUPPORTED_UNVERIFIED = "unsupported_unverified"


class AlternateLineSupport(StrEnum):
    NONE_APPLICABLE = "none_applicable"  # e.g. a pure winner market has no line at all
    LADDER_CONFIRMED = "ladder_confirmed"  # a concrete multi-rung example was found
    LADDER_PROBABLE = "ladder_probable"  # inferred from contract-template grammar only
    UNKNOWN = "unknown"


class KalshiMarketFamilyRecord(BaseModel):
    model_config = {"frozen": True}

    family_id: str = Field(..., description="Unique, stable slug, e.g. 'game_winner'")
    display_name: str
    scope: MarketScope
    historical_confidence: EvidenceConfidence
    evidence_summary: str = Field(..., description="What was found and why it supports this entry")
    evidence_sources: tuple[str, ...] = Field(default_factory=tuple, description="Source URLs")
    ticker_pattern: str | None = Field(default=None, description="Discovered/inferred ticker shape, if known")
    contract_semantic_type: str = Field(..., description="e.g. 'binary YES/NO on a margin threshold'")
    boundary_handling: str | None = Field(default=None, description="Push/tie/settlement behavior, if evidenced")
    alternate_line_support: AlternateLineSupport
    required_probability_primitive: str | None = Field(
        default=None, description="The math object Milestone C must produce, for GAME_LEVEL CORE_V1 families only"
    )
    milestone_c_priority: MilestoneCPriority
    example_games_or_tickers: tuple[str, ...] = Field(default_factory=tuple)
    notes: str | None = None

    @model_validator(mode="after")
    def _core_v1_requires_confirmed_and_a_primitive(self) -> KalshiMarketFamilyRecord:
        if self.milestone_c_priority == MilestoneCPriority.CORE_V1:
            if self.historical_confidence != EvidenceConfidence.CONFIRMED:
                raise ValueError(
                    f"{self.family_id!r} is CORE_V1 but historical_confidence is "
                    f"{self.historical_confidence!r}, not CONFIRMED -- an unverified or merely "
                    f"probable family cannot be a first-class Milestone C target"
                )
            if not self.required_probability_primitive:
                raise ValueError(f"{self.family_id!r} is CORE_V1 but has no required_probability_primitive set")
        return self

    @model_validator(mode="after")
    def _futures_never_targets_the_game_model(self) -> KalshiMarketFamilyRecord:
        if self.scope == MarketScope.FUTURES and self.milestone_c_priority in (
            MilestoneCPriority.CORE_V1,
            MilestoneCPriority.LATER_GAME_MODEL,
        ):
            raise ValueError(
                f"{self.family_id!r} is scope=FUTURES but priority={self.milestone_c_priority!r} -- "
                f"futures families must be FUTURES_SEPARATE_ENGINE or UNSUPPORTED_UNVERIFIED, "
                f"never a single-game-model priority"
            )
        return self


# --- The registry itself -------------------------------------------------
# See docs/KALSHI_CFB_MARKET_AUDIT.md for the full evidence trail behind
# every entry -- this module intentionally keeps evidence_summary short and
# defers the detailed sourcing to that document.

KALSHI_CFB_MARKET_FAMILIES: tuple[KalshiMarketFamilyRecord, ...] = (
    # --- Game-level: CORE_V1 -------------------------------------------
    KalshiMarketFamilyRecord(
        family_id="game_winner",
        display_name="Game winner (moneyline-style)",
        scope=MarketScope.GAME_LEVEL,
        historical_confidence=EvidenceConfidence.CONFIRMED,
        evidence_summary=(
            "KXNCAAFGAME series confirmed via multiple real, distinct tickers spanning marquee and "
            "non-marquee games; quoted cent pricing (e.g. Texas 52.5c / Ohio State 52.5c) confirms "
            "binary YES/NO settling at $1/$0 with cent price read directly as probability."
        ),
        evidence_sources=(
            "https://kalshi.com/markets/kxncaafgame/college-football-game",
            "https://kalshi.com/markets/kxncaafgame/college-football-game/kxncaafgame-26jan09oreind",
            "https://kalshi.com/markets/kxncaafgame/college-football-game/kxncaafgame-25dec19kennwmu",
            "https://kalshi.com/markets/kxncaafgame/college-football-game/kxncaafgame-26aug29unctcu",
        ),
        ticker_pattern="kxncaafgame-{yy}{mon}{dd}{away_code}{home_code}",
        contract_semantic_type="Binary YES/NO per team; cent price = implied win probability; no separate vig",
        boundary_handling="A tie (essentially impossible under current NCAA OT rules) settles both sides at 50c.",
        alternate_line_support=AlternateLineSupport.NONE_APPLICABLE,
        required_probability_primitive="P(home_score > away_score)",
        milestone_c_priority=MilestoneCPriority.CORE_V1,
        example_games_or_tickers=(
            "kxncaafgame-26jan09oreind (Oregon at Indiana)",
            "kxncaafgame-25dec31michtex (Michigan at Texas)",
            "kxncaafgame-25dec30ccarlt (Coastal Carolina at Louisiana Tech)",
            "kxncaafgame-25dec19kennwmu (Kennesaw St. at Western Michigan)",
            "kxncaafgame-26jan02ricetxst (Rice at Texas St.)",
            "kxncaafgame-26aug29unctcu (North Carolina vs TCU)",
        ),
    ),
    KalshiMarketFamilyRecord(
        family_id="point_spread",
        display_name="Point spread",
        scope=MarketScope.GAME_LEVEL,
        historical_confidence=EvidenceConfidence.CONFIRMED,
        evidence_summary=(
            "CFTC self-certification filed 2025-08-18 for football (college and pro) point-spread "
            "contracts, quoted template: 'Will <team> win <game> by <above/below/between/exactly/at "
            "least> <count> points?'. A live example (line -4.5, Yes 50c / No 51c) corroborates the "
            "template is actually listed, not just filed."
        ),
        evidence_sources=(
            "https://www.ingame.com/kalshi-self-certify-football-props-spreads/",
            "https://sports.yahoo.com/article/kalshi-self-certifies-offer-football-154252194.html",
            "https://www.oddsshopper.com/articles/prediction-markets/how-to-bet-college-football-on-kalshi",
        ),
        ticker_pattern=None,
        contract_semantic_type=(
            "Binary YES/NO on a signed margin threshold; the above/below/between/exactly/at-least "
            "modifier set suggests several distinct threshold contracts can coexist for one game, "
            "but a concrete multi-rung single-game example was not found (see alternate_line_support)."
        ),
        boundary_handling=(
            "PROBABLE, not directly CFB-quoted: reporting on the same football contract family "
            "describes push-like settlement at 'the last fair market price before the start of "
            "play' rather than a sportsbook-style refund; the direct quote was in an NFL context, "
            "not confirmed word-for-word for a CFB spread market specifically."
        ),
        alternate_line_support=AlternateLineSupport.LADDER_PROBABLE,
        required_probability_primitive="Margin distribution: P(home_score - away_score > threshold), per modifier",
        milestone_c_priority=MilestoneCPriority.CORE_V1,
        example_games_or_tickers=(),
        notes=(
            "'exactly N points' as a template modifier folds a sportsbook-style 'winning margin' "
            "market into this same family rather than it being distinct -- see registry entry "
            "winning_margin_exact_score below."
        ),
    ),
    KalshiMarketFamilyRecord(
        family_id="game_total",
        display_name="Game total (over/under)",
        scope=MarketScope.GAME_LEVEL,
        historical_confidence=EvidenceConfidence.CONFIRMED,
        evidence_summary=(
            "Same 2025-08-18 CFTC self-certification, quoted template: 'Will <game> have <over/under> "
            "<count> points in <time_period> of <game>?'. A live example (line 45.5, both sides ~51c) "
            "corroborates real listing, not just the filing."
        ),
        evidence_sources=(
            "https://www.ingame.com/kalshi-self-certify-football-props-spreads/",
            "https://www.oddsshopper.com/articles/prediction-markets/how-to-bet-college-football-on-kalshi",
        ),
        ticker_pattern=None,
        contract_semantic_type="Binary YES/NO on a total-points threshold for a specified time_period",
        boundary_handling=(
            "PROBABLE, by analogy to point_spread's settlement mechanism -- not independently "
            "quoted for totals."
        ),
        alternate_line_support=AlternateLineSupport.LADDER_PROBABLE,
        required_probability_primitive="Total-score distribution: P(home_score + away_score > threshold)",
        milestone_c_priority=MilestoneCPriority.CORE_V1,
        example_games_or_tickers=(),
    ),
    # --- Game-level: LATER_GAME_MODEL -----------------------------------
    KalshiMarketFamilyRecord(
        family_id="first_half_total",
        display_name="First-half total",
        scope=MarketScope.GAME_LEVEL,
        historical_confidence=EvidenceConfidence.PROBABLE,
        evidence_summary=(
            "A live Kalshi category page (kalshi.com/sports/football/all/1st-half-total) confirms this "
            "market TYPE exists for 'football'; the game_total contract template's <time_period> "
            "parameter explicitly generalizes to sub-periods. Not independently confirmed as "
            "CFB-specific (vs. NFL-only) at time of evidence gathering."
        ),
        evidence_sources=("https://kalshi.com/sports/football/all/1st-half-total",),
        ticker_pattern=None,
        contract_semantic_type="Binary YES/NO on a total-points threshold restricted to the first half",
        boundary_handling=None,
        alternate_line_support=AlternateLineSupport.UNKNOWN,
        required_probability_primitive="First-half total-score distribution",
        milestone_c_priority=MilestoneCPriority.LATER_GAME_MODEL,
        example_games_or_tickers=(),
    ),
    # --- Game-level: UNSUPPORTED_UNVERIFIED ------------------------------
    KalshiMarketFamilyRecord(
        family_id="first_half_spread",
        display_name="First-half spread",
        scope=MarketScope.GAME_LEVEL,
        historical_confidence=EvidenceConfidence.UNVERIFIED,
        evidence_summary=(
            "The point_spread template's <time_period> parameter implies this is technically "
            "self-certifiable, but no direct example, page, or explicit CFB confirmation was found."
        ),
        evidence_sources=(),
        ticker_pattern=None,
        contract_semantic_type="Unknown -- inferred only from a template grammar parameter",
        boundary_handling=None,
        alternate_line_support=AlternateLineSupport.UNKNOWN,
        required_probability_primitive=None,
        milestone_c_priority=MilestoneCPriority.UNSUPPORTED_UNVERIFIED,
        example_games_or_tickers=(),
    ),
    KalshiMarketFamilyRecord(
        family_id="first_quarter_markets",
        display_name="First-quarter winner/spread/total",
        scope=MarketScope.GAME_LEVEL,
        historical_confidence=EvidenceConfidence.UNVERIFIED,
        evidence_summary="No direct evidence found for a first-quarter-specific CFB market at all.",
        evidence_sources=(),
        contract_semantic_type="Unknown",
        alternate_line_support=AlternateLineSupport.UNKNOWN,
        milestone_c_priority=MilestoneCPriority.UNSUPPORTED_UNVERIFIED,
    ),
    KalshiMarketFamilyRecord(
        family_id="team_total",
        display_name="Team total (single team's own point total)",
        scope=MarketScope.GAME_LEVEL,
        historical_confidence=EvidenceConfidence.UNVERIFIED,
        evidence_summary="No direct evidence found of a Kalshi CFB team-total market distinct from the game total.",
        evidence_sources=(),
        contract_semantic_type="Unknown",
        alternate_line_support=AlternateLineSupport.UNKNOWN,
        milestone_c_priority=MilestoneCPriority.UNSUPPORTED_UNVERIFIED,
    ),
    KalshiMarketFamilyRecord(
        family_id="winning_margin_exact_score",
        display_name="Winning margin / exact score / score bands",
        scope=MarketScope.GAME_LEVEL,
        historical_confidence=EvidenceConfidence.PROBABLE,
        evidence_summary=(
            "No distinct family found -- the point_spread contract template's 'exactly' and 'between' "
            "modifiers already cover what a sportsbook would separately call a winning-margin or "
            "score-band market. Treated as folded into point_spread, not a separate family."
        ),
        evidence_sources=("https://www.ingame.com/kalshi-self-certify-football-props-spreads/",),
        contract_semantic_type="Same as point_spread, via the 'exactly'/'between' modifiers",
        alternate_line_support=AlternateLineSupport.LADDER_PROBABLE,
        milestone_c_priority=MilestoneCPriority.UNSUPPORTED_UNVERIFIED,
        notes=(
            "Not a build target on its own -- see point_spread. Kept as a registry entry so this "
            "question isn't silently unanswered."
        ),
    ),
    KalshiMarketFamilyRecord(
        family_id="touchdown_prop",
        display_name="Touchdown scorer prop",
        scope=MarketScope.GAME_LEVEL,
        historical_confidence=EvidenceConfidence.CONFIRMED,
        evidence_summary=(
            "CFTC self-certification filed 2025-08-25 for BOTH college and pro football, quoted "
            "template: 'Will <player/team> score <first/last/any/count> touchdown(s) <count> in "
            "<time_period> of <game>?'. However, reporting directly states actual rollout to college "
            "player props is NOT happening this season ('Kalshi does not appear likely to offer prop "
            "bets on college players this season... the actual rollout for college props appears "
            "limited') -- legally permitted, but not actually listed for CFB."
        ),
        evidence_sources=(
            "https://www.ingame.com/kalshi-nfl-player-props-college-ncaa/",
            "https://www.ingame.com/kalshi-self-certify-football-props-spreads/",
        ),
        contract_semantic_type="Binary YES/NO on a player or team scoring a touchdown in a time period",
        alternate_line_support=AlternateLineSupport.NONE_APPLICABLE,
        milestone_c_priority=MilestoneCPriority.UNSUPPORTED_UNVERIFIED,
        notes="CONFIRMED self-certified, but CONFIRMED NOT actually offered for college players -- do not build.",
    ),
    # --- Futures / season-long: FUTURES_SEPARATE_ENGINE ------------------
    KalshiMarketFamilyRecord(
        family_id="national_champion",
        display_name="National champion",
        scope=MarketScope.FUTURES,
        historical_confidence=EvidenceConfidence.CONFIRMED,
        evidence_summary="KXNCAAF series; real, dated volume figures (9M+ contracts across 50 teams as of 2026-08-01).",
        evidence_sources=("https://kalshi.com/markets/kxncaaf/ncaaf-championship/kxncaaf-27",),
        ticker_pattern="kxncaaf-{season}",
        contract_semantic_type="Binary YES/NO per team, one contract per candidate team",
        alternate_line_support=AlternateLineSupport.NONE_APPLICABLE,
        milestone_c_priority=MilestoneCPriority.FUTURES_SEPARATE_ENGINE,
        notes="Requires full season simulation, not a single-game model.",
    ),
    KalshiMarketFamilyRecord(
        family_id="conference_champion",
        display_name="Conference champion",
        scope=MarketScope.FUTURES,
        historical_confidence=EvidenceConfidence.CONFIRMED,
        evidence_summary=(
            "Confirmed via real tickers for ACC, SEC, Conference USA. Big Ten and Big 12 boards are "
            "described generically by secondary sources but no direct ticker was captured for them -- "
            "treat those two specifically as PROBABLE, not independently CONFIRMED."
        ),
        evidence_sources=(
            "https://kalshi.com/markets/kxncaafacc/acc-champion/kxncaafacc-26",
            "https://kalshi.com/markets/kxncaafsec/sec-champion/kxncaafsec-26",
            "https://kalshi.com/markets/kxncaafcusa/conference-usa-champion/kxncaafcusa-26",
        ),
        ticker_pattern="kxncaaf{conference_code}-{season}",
        contract_semantic_type="Binary YES/NO per team within one conference's board",
        alternate_line_support=AlternateLineSupport.NONE_APPLICABLE,
        milestone_c_priority=MilestoneCPriority.FUTURES_SEPARATE_ENGINE,
    ),
    KalshiMarketFamilyRecord(
        family_id="playoff_qualifier",
        display_name="CFP qualifier",
        scope=MarketScope.FUTURES,
        historical_confidence=EvidenceConfidence.CONFIRMED,
        evidence_summary="KXNCAAFPLAYOFF series confirmed via a real ticker.",
        evidence_sources=("https://kalshi.com/markets/kxncaafplayoff/college-football-playoff-qualifiers/kxncaafplayoff-26",),
        ticker_pattern="kxncaafplayoff-{season}",
        contract_semantic_type="Binary YES/NO per team",
        alternate_line_support=AlternateLineSupport.NONE_APPLICABLE,
        milestone_c_priority=MilestoneCPriority.FUTURES_SEPARATE_ENGINE,
    ),
    KalshiMarketFamilyRecord(
        family_id="heisman",
        display_name="Heisman Trophy winner",
        scope=MarketScope.FUTURES,
        historical_confidence=EvidenceConfidence.CONFIRMED,
        evidence_summary="KXHEISMAN series confirmed via a real ticker.",
        evidence_sources=("https://kalshi.com/markets/kxheisman/heisman-trophy-winner/kxheisman-27",),
        ticker_pattern="kxheisman-{season}",
        contract_semantic_type="Binary YES/NO per candidate player",
        alternate_line_support=AlternateLineSupport.NONE_APPLICABLE,
        milestone_c_priority=MilestoneCPriority.FUTURES_SEPARATE_ENGINE,
        notes="Requires a player/award model, not a game-score model.",
    ),
    KalshiMarketFamilyRecord(
        family_id="ap_poll_rank",
        display_name="AP Poll ranking (No. 1 and Top-25)",
        scope=MarketScope.FUTURES,
        historical_confidence=EvidenceConfidence.CONFIRMED,
        evidence_summary=(
            "Two distinct weekly series confirmed via real tickers: KXNCAAFAPRANK (who is No. 1 in a "
            "given week) and KXNCAAFTOPAPRANK (who is Top-25 in a given week). Graded weekly against "
            "the official AP poll release, per direct reporting."
        ),
        evidence_sources=(
            "https://kalshi.com/markets/kxncaafaprank/college-football-ap-rank/kxncaafaprank-26w1r1",
            "https://kalshi.com/markets/kxncaaftopaprank/college-football-ap-poll-top-rank/kxncaaftopaprank-26w1t25",
        ),
        ticker_pattern="kxncaafaprank-{season}w{week}r1 / kxncaaftopaprank-{season}w{week}t25",
        contract_semantic_type="Binary YES/NO per team, regraded weekly against the real AP release",
        alternate_line_support=AlternateLineSupport.NONE_APPLICABLE,
        milestone_c_priority=MilestoneCPriority.FUTURES_SEPARATE_ENGINE,
        notes="Requires a polling/ranking model, not a single-game score model.",
    ),
    KalshiMarketFamilyRecord(
        family_id="regular_season_win_total",
        display_name="Regular-season win total (ladder)",
        scope=MarketScope.FUTURES,
        historical_confidence=EvidenceConfidence.CONFIRMED,
        evidence_summary=(
            "KXNCAAFWINS series; directly quoted as an explicit multi-rung ladder, e.g. Alabama listing "
            "8+, 9+, 10+, 11+, and 12 wins as SEPARATE simultaneous contracts (not a single line), "
            "covering 69 teams with 462,000+ contracts traded before kickoff. This is the strongest "
            "concrete evidence found anywhere in this audit for genuine alternate-rung ladder behavior "
            "-- but it is a season future, not a single-game spread/total."
        ),
        evidence_sources=(
            "https://kalshi.com/category/sports/football/ncaa-football/win-totals",
            "https://nexteventhorizon.substack.com/p/kalshi-expands-sports-betting-menu",
        ),
        ticker_pattern="kxncaafwins-{season}{team_code}",
        contract_semantic_type=(
            "Ladder of binary YES/NO 'N or more wins' contracts, several rungs simultaneously listed"
        ),
        alternate_line_support=AlternateLineSupport.LADDER_CONFIRMED,
        milestone_c_priority=MilestoneCPriority.FUTURES_SEPARATE_ENGINE,
        notes="Requires season simulation (win-count distribution over a full schedule), not a single-game model.",
    ),
    KalshiMarketFamilyRecord(
        family_id="undefeated_season",
        display_name="Undefeated season",
        scope=MarketScope.FUTURES,
        historical_confidence=EvidenceConfidence.PROBABLE,
        evidence_summary=(
            "Repeatedly named in aggregate market-family descriptions across multiple independent "
            "sources, but no specific ticker was independently captured in this audit."
        ),
        evidence_sources=(),
        contract_semantic_type="Presumed binary YES/NO per team",
        alternate_line_support=AlternateLineSupport.NONE_APPLICABLE,
        milestone_c_priority=MilestoneCPriority.FUTURES_SEPARATE_ENGINE,
    ),
    KalshiMarketFamilyRecord(
        family_id="coach_market",
        display_name="Coach fired / next coach",
        scope=MarketScope.FUTURES,
        historical_confidence=EvidenceConfidence.CONFIRMED,
        evidence_summary=(
            "Confirmed via multiple concrete, real examples: a 'which head coach will be fired before "
            "Week 1' market (Bill Belichick at North Carolina quoted at 12%), and a 'next Michigan head "
            "coach' market with named candidates and live percentages."
        ),
        evidence_sources=(
            "https://www.si.com/prediction-markets/college/kalshi-college-football-coach-market-senses-trouble-for-bill-belichick-01kt75g59230",
            "https://news.kalshi.com/p/michigan-next-head-coach-odds",
        ),
        ticker_pattern=None,
        contract_semantic_type=(
            "Binary YES/NO, appears to be ad hoc per-program markets rather than one fixed "
            "recurring series"
        ),
        alternate_line_support=AlternateLineSupport.NONE_APPLICABLE,
        milestone_c_priority=MilestoneCPriority.FUTURES_SEPARATE_ENGINE,
        notes="Requires a coaching-change/personnel model entirely outside game-score projection.",
    ),
    KalshiMarketFamilyRecord(
        family_id="fcs_national_champion",
        display_name="FCS national champion",
        scope=MarketScope.FUTURES,
        historical_confidence=EvidenceConfidence.CONFIRMED,
        evidence_summary=(
            "KXNCAAFCS series confirmed via a real ticker. Notable as evidence Kalshi tracks FCS as "
            "its own separate futures universe -- relevant context for the FBS-vs-FCS single-game "
            "coverage question (see docs/KALSHI_CFB_MARKET_AUDIT.md), though it does not itself answer "
            "whether individual FBS-vs-FCS GAMES get single-game markets."
        ),
        evidence_sources=("https://kalshi.com/markets/kxncaafcs/fcs-football-champion/kxncaafcs-25",),
        ticker_pattern="kxncaafcs-{season}",
        contract_semantic_type="Binary YES/NO per FCS team",
        alternate_line_support=AlternateLineSupport.NONE_APPLICABLE,
        milestone_c_priority=MilestoneCPriority.FUTURES_SEPARATE_ENGINE,
    ),
)


def validate_registry(records: tuple[KalshiMarketFamilyRecord, ...] = KALSHI_CFB_MARKET_FAMILIES) -> None:
    """Mechanical, whole-registry invariants beyond what a single record's
    own validators can check in isolation (uniqueness). Per-record rules
    (CORE_V1 requires CONFIRMED + a primitive; FUTURES never gets a
    game-model priority) are enforced by KalshiMarketFamilyRecord's own
    model_validators and therefore can't even construct a bad record.
    """
    ids = [r.family_id for r in records]
    if len(ids) != len(set(ids)):
        duplicates = {i for i in ids if ids.count(i) > 1}
        raise ValueError(f"duplicate family_id(s) in registry: {duplicates!r}")


validate_registry()
