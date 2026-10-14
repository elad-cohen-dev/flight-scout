"""Buy or wait (app/advice.py): every rule of the verdict, the level and trend
helpers, and the builders for searches and watches. Pure — no DB, no network."""

from __future__ import annotations

from datetime import date

import pytest

from app import advice
from app.advice import BUY, GOOD, UNKNOWN, WAIT, Signals
from app.models import PriceInsights, WatchStats

TODAY = date(2026, 10, 14)
RANGE = dict(typical_low=300.0, typical_high=400.0)


def verdict(price, days, **kw):
    return advice.advise(Signals(price=price, currency="USD", days_out=days, **kw))


# --- where the price sits ---------------------------------------------------------

@pytest.mark.parametrize("price, level", [(250, "low"), (299.99, "low"), (300, "typical"), (400, "typical"),
                                          (401, "high")])
def test_level_against_googles_range(price, level):
    assert advice.level(Signals(price=price, currency="USD", **RANGE))[0] == level


@pytest.mark.parametrize("price, level", [(270, "low"), (280, "typical"), (319, "typical"), (330, "high")])
def test_level_against_the_watch_typical(price, level):
    assert advice.level(Signals(price=price, currency="USD", typical=300))[0] == level


def test_range_wins_over_typical_and_googles_word():
    s = Signals(price=350, currency="USD", typical=500, price_level="high", **RANGE)
    assert advice.level(s) == ("typical", "inside Google's typical $300–400")


def test_googles_word_when_nothing_else():
    assert advice.level(Signals(price=350, currency="USD", price_level="low")) == ("low", "Google rates the fare low")
    assert advice.level(Signals(price=350, currency="USD", price_level="unknown")) == ("", "")
    assert advice.level(Signals(price=350, currency="USD")) == ("", "")


def test_level_wording_has_the_numbers():
    assert advice.level(Signals(price=255, currency="USD", **RANGE))[1] == "15% under Google's typical $300–400"
    assert advice.level(Signals(price=1300, currency="ILS", typical=1000))[1] == "30% over the typical ₪1,000"


# --- trend ---------------------------------------------------------------------------

@pytest.mark.parametrize("runs, word", [
    ([], ""), ([300, 320], ""),                        # too few runs
    ([300, 310, 330], "rising"), ([330, 310, 300], "falling"),
    ([300, 300, 305], "flat"),                          # under 5% end to end
    ([300, 350, 320], "flat"),                          # not one way
    ([999, 300, 310, 330], "rising"),                  # only the last three count
])
def test_trend(runs, word):
    assert advice.trend(runs) == word


# --- verdicts ------------------------------------------------------------------------

def test_low_price_close_to_departure_is_buy_now():
    a = verdict(250, 20, **RANGE)
    assert (a.verdict, a.label) == (BUY, "Buy now")
    assert a.reason == "17% under Google's typical $300–400, 20 days out — short-haul fares usually climb from here"
    assert a.days_out == 20


def test_low_price_far_out_is_a_good_price_unless_a_30_day_low():
    assert verdict(250, 80, **RANGE).verdict == GOOD
    assert "it could still dip" in verdict(250, 80, **RANGE).reason
    new_low = verdict(250, 80, low_30d=260, **RANGE)
    assert new_low.verdict == BUY and new_low.reason.endswith("and the lowest in 30 days")
    assert verdict(250, 80, low_30d=240, **RANGE).verdict == GOOD


def test_typical_price_by_days_out():
    assert verdict(350, 10, **RANGE).verdict == BUY       # last minute
    soon = verdict(350, 30, **RANGE)
    assert soon.verdict == GOOD and "book soon" in soon.reason and "~5 weeks" in soon.reason
    far = verdict(350, 70, **RANGE)
    assert far.verdict == WAIT and "no rush" in far.reason


def test_typical_price_rising_is_a_good_price():
    a = verdict(350, 70, recent=(320, 335, 350), **RANGE)
    assert a.verdict == GOOD and "rising over your last checks" in a.reason
    assert verdict(350, 70, recent=(380, 365, 350), **RANGE).verdict == WAIT


def test_high_price():
    assert verdict(500, 7, **RANGE).verdict == BUY       # waiting rarely helps a week out
    far = verdict(500, 90, **RANGE)
    assert far.verdict == WAIT and far.reason.endswith("wait for a dip")
    near = verdict(500, 25, **RANGE)
    assert near.verdict == WAIT and "don't wait past ~5 weeks out" in near.reason
    assert "and falling" in verdict(500, 90, recent=(560, 530, 500), **RANGE).reason


def test_long_haul_rises_earlier():
    assert verdict(350, 50, **RANGE).verdict == WAIT
    long = verdict(350, 50, long_haul=True, **RANGE)
    assert long.verdict == GOOD and "long-haul" in long.reason and "~8 weeks" in long.reason


def test_not_enough_data():
    a = verdict(350, 60)
    assert (a.verdict, a.label) == (UNKNOWN, "Not enough data")
    assert verdict(350, None).verdict == UNKNOWN
    # ...but inside the last-minute window waiting for data costs more.
    assert verdict(350, 5).verdict == BUY and "no price history" in verdict(350, 5).reason


def test_unknown_departure_is_treated_as_far_out():
    a = verdict(250, None, **RANGE)
    assert a.verdict == GOOD and "days out" not in a.reason and a.days_out is None
    assert verdict(350, None, **RANGE).verdict == WAIT


def test_past_departure():
    assert verdict(250, -1, **RANGE).verdict == UNKNOWN


def test_reason_is_one_capitalised_line():
    for days in (None, 0, 1, 10, 30, 80):
        for price in (250, 350, 500):
            a = verdict(price, days, **RANGE)
            assert "\n" not in a.reason and not a.reason[0].islower() and a.label == advice.LABELS[a.verdict]
    assert "tomorrow" in verdict(250, 1, **RANGE).reason


# --- builders ------------------------------------------------------------------------

def insights(**kw):
    return PriceInsights(**{"route": "TLV>LHR", "currency": "USD", "price_level": "typical",
                            "typical_low": 300, "typical_high": 400, **kw})


def test_for_search_shifts_googles_range_by_the_bag_fees():
    # $420 with bags = $370 fare + $50 bags: typical once the range moves by the bags too.
    a = advice.for_search(420, "USD", "2026-12-20", TODAY, insights(), bag_offset=50,
                          origin="TLV", destination="LHR")
    assert a.verdict == WAIT and "$350–450" in a.reason and a.days_out == 67
    assert advice.for_search(420, "USD", "2026-12-20", TODAY, insights()).verdict == WAIT  # high without the shift
    assert "over" in advice.for_search(420, "USD", "2026-12-20", TODAY, insights()).reason


def test_for_search_other_currency_or_no_price():
    assert advice.for_search(None, "USD", "2026-12-20", TODAY, insights()) is None
    a = advice.for_search(1200, "ILS", "2026-12-20", TODAY, insights())  # insights in USD: not comparable
    assert a.verdict == UNKNOWN
    assert advice.for_search(1200, "ILS", "2026-11-01", TODAY, None).verdict == UNKNOWN  # 18 days: not last minute
    assert advice.for_search(1200, "ILS", "2026-10-24", TODAY, None).verdict == BUY  # 10 days: it is


def test_for_search_without_data_far_out():
    assert advice.for_search(300, "USD", "2027-01-01", TODAY, None).verdict == UNKNOWN


def test_long_haul_by_distance():
    assert advice.long_haul("TLV", "JFK") is True
    assert advice.long_haul("TLV", "LHR") is False
    assert advice.long_haul("TLV", "Nowhere-ville") is False


def stats(**kw):
    base = dict(currency="USD", runs=6, last_best=300, low_30d=290, typical=330, basis="verified", basis_runs=6)
    return WatchStats(**{**base, **kw})


def test_for_watch_uses_its_own_typical_once_deep_enough():
    a = advice.for_watch(stats(), [310, 300], date(2027, 1, 20), TODAY, "TLV", "LHR")
    assert a.verdict == WAIT and "About the typical $330" in a.reason
    thin = advice.for_watch(stats(basis_runs=2, runs=2), [310, 300], date(2027, 1, 20), TODAY)
    assert thin.verdict == UNKNOWN  # two runs aren't "typical"
    legacy = advice.for_watch(stats(basis="legacy"), [310, 300], date(2027, 1, 20), TODAY)
    assert legacy.verdict == UNKNOWN  # unverified estimates never advise


def test_for_watch_on_googles_basis():
    s = stats(basis="google", basis_runs=1, history_points=20, typical_low=350, typical_high=450, typical=400)
    a = advice.for_watch(s, [300], date(2026, 11, 10), TODAY, "TLV", "LHR")
    assert a.verdict == BUY and "under Google's typical $350–450" in a.reason  # 27 days out


def test_for_watch_new_low_needs_the_low_before_this_price():
    s = stats(last_best=280, low_30d=280, typical=330)
    assert advice.for_watch(s, [300, 280], date(2027, 1, 20), TODAY, price=280, prior_low=290).verdict == BUY
    assert advice.for_watch(s, [300, 280], date(2027, 1, 20), TODAY, price=280, prior_low=270).verdict == GOOD


def test_for_watch_nothing_to_say():
    assert advice.for_watch(None, [], None, TODAY) is None
    assert advice.for_watch(stats(last_best=None), [], None, TODAY) is None
    assert advice.days_until("mid November", TODAY) is None and advice.days_until("", TODAY) is None
    assert advice.days_until(date(2026, 10, 20), TODAY) == 6


def test_typical_band_but_best_seen_is_not_wait():
    # 9% under the typical (inside the ±10% band) yet the lowest of 30 days:
    # "Wait" would contradict the watch's own deal score.
    s = advice.Signals(price=2864, currency="ILS", days_out=62, typical=3159, low_30d=2864)
    assert advice.advise(s).verdict == advice.GOOD
    great = advice.Signals(price=2950, currency="ILS", days_out=62, typical=3159, score=91)
    assert advice.advise(great).verdict == advice.GOOD
    plain = advice.Signals(price=3100, currency="ILS", days_out=62, typical=3159, score=55)
    assert advice.advise(plain).verdict == advice.WAIT
