"""Weather risk classification and route ranking.

Pure functions only (no I/O) so the rules are easy to read and unit test.

Thresholds (from the assessment brief):

    Condition  Low        Moderate    High       Severe      No Travel
    Wind       <25 mph    25-34       35-44      45-54       >=55 mph
    Rain       <0.10 in/h 0.10-0.25   0.25-0.50  0.50-1.00   >1.00
    Snow       <0.5 in/h  0.5-1.0     1.0-2.0    2.0-3.0     >3.0

Band edges are interpreted as half-open intervals so every value maps to exactly one
level: e.g. wind 34.6 mph is Moderate (it is below 35), rain exactly 0.25 in/h is High,
rain exactly 1.00 in/h is still Severe (No Travel is strictly ">1.00").

Load rules:
    * wind >= 55 mph                    -> No Travel (any load)
    * 45-54 mph and load > 30,000 lb    -> No Travel
    * 35-44 mph and load > 40,000 lb    -> Severe
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Iterable, Sequence


class Risk(IntEnum):
    LOW = 0
    MODERATE = 1
    HIGH = 2
    SEVERE = 3
    NO_TRAVEL = 4

    @property
    def label(self) -> str:
        return RISK_LABELS[self]


RISK_LABELS = {
    Risk.LOW: "Low",
    Risk.MODERATE: "Moderate",
    Risk.HIGH: "High",
    Risk.SEVERE: "Severe",
    Risk.NO_TRAVEL: "No Travel",
}

# Lower bounds (inclusive) for Moderate, High, Severe. No Travel handled separately
# because its edge is inclusive for wind (>=55) but exclusive for rain/snow (>1.00, >3.0).
WIND_BANDS = (25.0, 35.0, 45.0)
WIND_NO_TRAVEL = 55.0  # inclusive
RAIN_BANDS = (0.10, 0.25, 0.50)
RAIN_NO_TRAVEL = 1.00  # exclusive
SNOW_BANDS = (0.5, 1.0, 2.0)
SNOW_NO_TRAVEL = 3.0  # exclusive

LOAD_NO_TRAVEL_LB = 30_000  # with 45-54 mph wind
LOAD_SEVERE_LB = 40_000  # with 35-44 mph wind


def _banded(value: float, bands: Sequence[float]) -> Risk:
    moderate, high, severe = bands
    if value >= severe:
        return Risk.SEVERE
    if value >= high:
        return Risk.HIGH
    if value >= moderate:
        return Risk.MODERATE
    return Risk.LOW


def classify_wind(mph: float) -> Risk:
    if mph >= WIND_NO_TRAVEL:
        return Risk.NO_TRAVEL
    return _banded(mph, WIND_BANDS)


def classify_rain(in_per_hr: float) -> Risk:
    if in_per_hr > RAIN_NO_TRAVEL:
        return Risk.NO_TRAVEL
    return _banded(in_per_hr, RAIN_BANDS)


def classify_snow(in_per_hr: float) -> Risk:
    if in_per_hr > SNOW_NO_TRAVEL:
        return Risk.NO_TRAVEL
    return _banded(in_per_hr, SNOW_BANDS)


def apply_load_rules(wind_mph: float, load_lb: float, wind_risk: Risk) -> tuple[Risk, str | None]:
    """Escalate the wind risk for heavy loads. Returns (risk, reason-if-escalated)."""
    if wind_mph >= WIND_NO_TRAVEL:
        return Risk.NO_TRAVEL, None  # already No Travel for any load
    if wind_mph >= 45 and load_lb > LOAD_NO_TRAVEL_LB:
        return Risk.NO_TRAVEL, f"{wind_mph:.0f} mph wind with load over {LOAD_NO_TRAVEL_LB:,} lb"
    if wind_mph >= 35 and load_lb > LOAD_SEVERE_LB and wind_risk < Risk.SEVERE:
        return Risk.SEVERE, f"{wind_mph:.0f} mph wind with load over {LOAD_SEVERE_LB:,} lb"
    return wind_risk, None


@dataclass(frozen=True)
class CheckpointRisk:
    risk: Risk
    wind: Risk
    rain: Risk
    snow: Risk
    drivers: tuple[str, ...]  # human readable reasons for the final level


def classify_conditions(wind_mph: float, rain_in_hr: float, snow_in_hr: float, load_lb: float) -> CheckpointRisk:
    """Checkpoint risk = worst of wind (after load rules), rain and snow."""
    wind_base = classify_wind(wind_mph)
    wind, load_reason = apply_load_rules(wind_mph, load_lb, wind_base)
    rain = classify_rain(rain_in_hr)
    snow = classify_snow(snow_in_hr)
    overall = max(wind, rain, snow)

    drivers: list[str] = []
    if overall > Risk.LOW:
        if wind == overall:
            drivers.append(load_reason or f"Wind {wind_mph:.0f} mph")
        if rain == overall:
            drivers.append(f"Rain {rain_in_hr:.2f} in/hr")
        if snow == overall:
            drivers.append(f"Snow {snow_in_hr:.2f} in/hr")
    return CheckpointRisk(overall, wind, rain, snow, tuple(drivers))


# --------------------------------------------------------------------------- routes


@dataclass
class RouteRiskSummary:
    miles_by_risk: dict[Risk, float] = field(default_factory=lambda: {r: 0.0 for r in Risk})
    avg_risk: float = 0.0
    max_risk: Risk = Risk.LOW

    @property
    def severe_miles(self) -> float:
        return self.miles_by_risk[Risk.SEVERE]

    @property
    def no_travel_miles(self) -> float:
        return self.miles_by_risk[Risk.NO_TRAVEL]

    @property
    def high_miles(self) -> float:
        return self.miles_by_risk[Risk.HIGH]


def checkpoint_coverage(miles: Sequence[float], total_miles: float) -> list[float]:
    """Miles of road each checkpoint represents.

    Each checkpoint covers the road halfway to its neighbours, so coverage always sums to
    the route length regardless of interval (and the shorter final segment is weighted fairly).
    """
    n = len(miles)
    if n == 0:
        return []
    if n == 1:
        return [total_miles]
    cover = []
    for i, m in enumerate(miles):
        left = 0.0 if i == 0 else (m + miles[i - 1]) / 2
        right = total_miles if i == n - 1 else (m + miles[i + 1]) / 2
        cover.append(max(0.0, right - left))
    return cover


def summarize_route(miles: Sequence[float], risks: Sequence[Risk], total_miles: float) -> RouteRiskSummary:
    summary = RouteRiskSummary()
    cover = checkpoint_coverage(miles, total_miles)
    weighted = 0.0
    for c, r in zip(cover, risks):
        summary.miles_by_risk[r] += c
        weighted += c * int(r)
    covered = sum(cover)
    summary.avg_risk = weighted / covered if covered else 0.0
    summary.max_risk = max(risks) if risks else Risk.LOW
    return summary


@dataclass
class RankInput:
    route_id: str
    summary: RouteRiskSummary
    duration_s: float


# Average risk is on a 0-4 scale. Differences smaller than this are treated as "the same
# risk", so travel time decides (the brief asks to balance risk against time). Miles in
# Severe / High conditions are never traded for time.
AVG_RISK_TOLERANCE = 0.05


def ranking_key(item: RankInput) -> tuple:
    """Lexicographic preference from the brief, with No Travel treated as worse than Severe.

    1. fewest No Travel miles (a route through No Travel conditions is never preferred
       over one that avoids them)
    2. fewest Severe miles
    3. fewest High miles
    4. lowest average risk (within AVG_RISK_TOLERANCE counts as a tie)
    5. shortest travel time

    Miles are rounded to 0.1 mi so floating point noise never decides a tie.
    """
    s = item.summary
    return (
        round(s.no_travel_miles, 1),
        round(s.severe_miles, 1),
        round(s.high_miles, 1),
        round(s.avg_risk, 3),
        item.duration_s,
    )


def _tier(item: RankInput) -> tuple:
    return ranking_key(item)[:3]


def rank_routes(items: Iterable[RankInput]) -> list[RankInput]:
    """Order routes best-first.

    Routes are grouped by the mile-based criteria (1-3). Inside the best remaining group,
    every route whose average risk is within AVG_RISK_TOLERANCE of the group's lowest is
    considered equally safe, and the fastest of those is picked next.
    """
    remaining = sorted(items, key=ranking_key)
    ranked: list[RankInput] = []
    while remaining:
        tier = _tier(remaining[0])
        group = [r for r in remaining if _tier(r) == tier]
        lowest = min(r.summary.avg_risk for r in group)
        similar = [r for r in group if r.summary.avg_risk - lowest <= AVG_RISK_TOLERANCE + 1e-9]
        pick = min(similar, key=lambda r: (r.duration_s, r.summary.avg_risk))
        ranked.append(pick)
        remaining.remove(pick)
    return ranked


CRITERIA = ("No Travel miles", "Severe miles", "High miles", "average risk", "travel time")


def deciding_criterion(best: RankInput, other: RankInput) -> int:
    """Index into CRITERIA of the first criterion that separates `best` from `other`."""
    a, b = ranking_key(best), ranking_key(other)
    for i in range(3):
        if a[i] != b[i]:
            return i
    if abs(best.summary.avg_risk - other.summary.avg_risk) > AVG_RISK_TOLERANCE + 1e-9:
        return 3
    return 4


def _compare(best: RankInput, other: RankInput, name: str) -> str:
    s, o = best.summary, other.summary
    i = deciding_criterion(best, other)
    if i == 0:
        return f"avoids {o.no_travel_miles - s.no_travel_miles:.0f} mi of No Travel conditions on {name}"
    if i == 1:
        return f"fewer Severe miles than {name} ({s.severe_miles:.0f} vs {o.severe_miles:.0f} mi)"
    if i == 2:
        return f"fewer High miles than {name} ({s.high_miles:.0f} vs {o.high_miles:.0f} mi)"
    if i == 3:
        return f"lower average risk than {name} ({s.avg_risk:.2f} vs {o.avg_risk:.2f})"
    diff_min = (other.duration_s - best.duration_s) / 60
    faster = f"{_fmt_minutes(diff_min)} faster" if diff_min >= 1 else "about the same drive time"
    if abs(s.avg_risk - o.avg_risk) < 0.005:
        return f"same weather risk as {name} but {faster}" if diff_min >= 1 else f"same weather risk and time as {name}"
    if s.avg_risk < o.avg_risk:
        return f"slightly lower average risk than {name} ({s.avg_risk:.2f} vs {o.avg_risk:.2f}) and {faster}"
    return f"similar weather risk as {name} ({s.avg_risk:.2f} vs {o.avg_risk:.2f}) but {faster}"


def _fmt_minutes(minutes: float) -> str:
    if minutes < 60:
        return f"{minutes:.0f} min"
    return f"{int(minutes // 60)} h {int(round(minutes % 60)):02d} min"


def explain_recommendation(best: RankInput, others: Sequence[RankInput], names: dict[str, str] | None = None) -> str:
    """Short explanation of why `best` won against each other route, e.g.
    "Fewer Severe miles than Route B (0 vs 40 mi); lower average risk than Route C (0.10 vs 0.31)."
    """
    if not others:
        return "Only route available."
    names = names or {}
    parts = [_compare(best, o, names.get(o.route_id, f"Route {o.route_id}")) for o in others]
    text = "; ".join(parts)
    return text[0].upper() + text[1:] + "."
