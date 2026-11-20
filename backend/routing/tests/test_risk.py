import pytest

from routing.services.risk import (
    RankInput,
    Risk,
    RouteRiskSummary,
    apply_load_rules,
    checkpoint_coverage,
    classify_conditions,
    classify_rain,
    classify_snow,
    classify_wind,
    explain_recommendation,
    rank_routes,
    summarize_route,
)


@pytest.mark.parametrize(
    "mph, expected",
    [
        (0, Risk.LOW), (24.9, Risk.LOW), (25, Risk.MODERATE), (34.9, Risk.MODERATE),
        (35, Risk.HIGH), (44.9, Risk.HIGH), (45, Risk.SEVERE), (54.9, Risk.SEVERE),
        (55, Risk.NO_TRAVEL), (80, Risk.NO_TRAVEL),
    ],
)
def test_wind_bands(mph, expected):
    assert classify_wind(mph) == expected


@pytest.mark.parametrize(
    "inches, expected",
    [
        (0, Risk.LOW), (0.099, Risk.LOW), (0.10, Risk.MODERATE), (0.249, Risk.MODERATE),
        (0.25, Risk.HIGH), (0.499, Risk.HIGH), (0.50, Risk.SEVERE), (1.00, Risk.SEVERE),
        (1.001, Risk.NO_TRAVEL),
    ],
)
def test_rain_bands(inches, expected):
    assert classify_rain(inches) == expected


@pytest.mark.parametrize(
    "inches, expected",
    [
        (0, Risk.LOW), (0.49, Risk.LOW), (0.5, Risk.MODERATE), (0.99, Risk.MODERATE),
        (1.0, Risk.HIGH), (1.99, Risk.HIGH), (2.0, Risk.SEVERE), (3.0, Risk.SEVERE),
        (3.01, Risk.NO_TRAVEL),
    ],
)
def test_snow_bands(inches, expected):
    assert classify_snow(inches) == expected


class TestLoadRules:
    def test_55_mph_is_no_travel_for_any_load(self):
        assert apply_load_rules(55, 0, classify_wind(55))[0] == Risk.NO_TRAVEL
        assert classify_conditions(60, 0, 0, load_lb=1_000).risk == Risk.NO_TRAVEL

    def test_45_to_54_over_30k_is_no_travel(self):
        assert classify_conditions(45, 0, 0, load_lb=30_001).risk == Risk.NO_TRAVEL
        assert classify_conditions(54, 0, 0, load_lb=45_000).risk == Risk.NO_TRAVEL

    def test_45_to_54_at_or_under_30k_stays_severe(self):
        assert classify_conditions(50, 0, 0, load_lb=30_000).risk == Risk.SEVERE

    def test_35_to_44_over_40k_is_severe(self):
        assert classify_conditions(35, 0, 0, load_lb=40_001).risk == Risk.SEVERE
        assert classify_conditions(44, 0, 0, load_lb=80_000).risk == Risk.SEVERE

    def test_35_to_44_at_or_under_40k_stays_high(self):
        assert classify_conditions(40, 0, 0, load_lb=40_000).risk == Risk.HIGH

    def test_light_wind_unaffected_by_heavy_load(self):
        assert classify_conditions(30, 0, 0, load_lb=80_000).risk == Risk.MODERATE

    def test_load_reason_reported(self):
        result = classify_conditions(48, 0, 0, load_lb=35_000)
        assert result.risk == Risk.NO_TRAVEL
        assert "30,000" in result.drivers[0]


class TestCheckpointRisk:
    def test_worst_condition_wins(self):
        result = classify_conditions(20, 0.3, 0.6, load_lb=10_000)
        assert result.risk == Risk.HIGH
        assert result.rain == Risk.HIGH and result.snow == Risk.MODERATE and result.wind == Risk.LOW
        assert result.drivers == ("Rain 0.30 in/hr",)

    def test_ties_list_every_driver(self):
        result = classify_conditions(40, 0.3, 0, load_lb=10_000)
        assert result.risk == Risk.HIGH
        assert len(result.drivers) == 2

    def test_calm_has_no_drivers(self):
        result = classify_conditions(5, 0, 0, load_lb=10_000)
        assert result.risk == Risk.LOW and result.drivers == ()


class TestRouteSummary:
    def test_coverage_sums_to_route_length(self):
        miles = [0, 25, 50, 75, 90]
        cover = checkpoint_coverage(miles, 90)
        assert sum(cover) == pytest.approx(90)
        assert cover == pytest.approx([12.5, 25, 25, 20, 7.5])

    def test_summary_miles_and_average(self):
        miles = [0, 25, 50, 75, 100]
        risks = [Risk.LOW, Risk.LOW, Risk.HIGH, Risk.SEVERE, Risk.LOW]
        s = summarize_route(miles, risks, 100)
        assert s.miles_by_risk[Risk.HIGH] == pytest.approx(25)
        assert s.miles_by_risk[Risk.SEVERE] == pytest.approx(25)
        assert s.miles_by_risk[Risk.LOW] == pytest.approx(50)
        assert s.avg_risk == pytest.approx((25 * 2 + 25 * 3) / 100)
        assert s.max_risk == Risk.SEVERE


def _summary(no_travel=0.0, severe=0.0, high=0.0, avg=0.0):
    s = RouteRiskSummary()
    s.miles_by_risk[Risk.NO_TRAVEL] = no_travel
    s.miles_by_risk[Risk.SEVERE] = severe
    s.miles_by_risk[Risk.HIGH] = high
    s.avg_risk = avg
    return s


class TestRanking:
    def test_fewest_severe_beats_faster(self):
        slow_safe = RankInput("A", _summary(severe=0, high=100), 50_000)
        fast_risky = RankInput("B", _summary(severe=10), 40_000)
        assert rank_routes([fast_risky, slow_safe])[0].route_id == "A"

    def test_no_travel_is_worse_than_severe(self):
        a = RankInput("A", _summary(no_travel=5, severe=0), 40_000)
        b = RankInput("B", _summary(no_travel=0, severe=50), 45_000)
        assert rank_routes([a, b])[0].route_id == "B"

    def test_high_miles_break_severe_tie(self):
        a = RankInput("A", _summary(severe=10, high=40), 40_000)
        b = RankInput("B", _summary(severe=10, high=20), 45_000)
        assert rank_routes([a, b])[0].route_id == "B"

    def test_small_average_risk_gap_lets_time_decide(self):
        a = RankInput("A", _summary(high=20, avg=0.17), 27_000)
        b = RankInput("B", _summary(high=20, avg=0.14), 38_000)
        assert rank_routes([b, a])[0].route_id == "A"
        assert explain_recommendation(a, [b]).startswith("Similar weather risk as Route B (0.17 vs 0.14) but 3 h 03 min faster")

    def test_small_average_gap_never_beats_high_miles(self):
        a = RankInput("A", _summary(high=25, avg=0.14), 27_000)
        b = RankInput("B", _summary(high=20, avg=0.14), 38_000)
        assert rank_routes([a, b])[0].route_id == "B"

    def test_explanation_when_best_is_both_safer_and_faster(self):
        a = RankInput("A", _summary(avg=0.0), 30_000)
        b = RankInput("B", _summary(avg=0.03), 33_000)
        assert explain_recommendation(a, [b]) == (
            "Slightly lower average risk than Route B (0.00 vs 0.03) and 50 min faster."
        )

    def test_average_risk_breaks_high_tie(self):
        a = RankInput("A", _summary(high=20, avg=0.8), 40_000)
        b = RankInput("B", _summary(high=20, avg=0.5), 45_000)
        assert rank_routes([a, b])[0].route_id == "B"

    def test_travel_time_is_final_tiebreak(self):
        a = RankInput("A", _summary(avg=0.1), 45_000)
        b = RankInput("B", _summary(avg=0.1), 40_000)
        assert rank_routes([a, b])[0].route_id == "B"

    def test_float_noise_does_not_decide(self):
        a = RankInput("A", _summary(high=20.000001), 40_000)
        b = RankInput("B", _summary(high=20.0), 45_000)
        assert rank_routes([a, b])[0].route_id == "A"

    def test_explanations(self):
        best = RankInput("A", _summary(severe=0), 50_000)
        other = RankInput("B", _summary(severe=30), 40_000)
        assert explain_recommendation(best, [other]) == "Fewer Severe miles than Route B (0 vs 30 mi)."
        tie_best = RankInput("A", _summary(), 40_000)
        tie_other = RankInput("B", _summary(), 46_000)
        risky = RankInput("C", _summary(no_travel=12), 30_000)
        text = explain_recommendation(tie_best, [tie_other, risky], {"B": "Route B", "C": "Route C"})
        assert text == "Same weather risk as Route B but 1 h 40 min faster; avoids 12 mi of No Travel conditions on Route C."
