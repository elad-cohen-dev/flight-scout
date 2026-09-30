from datetime import date

from app.agent import build_research_prompt, describe_party, is_flexible, parse_iso_date, postprocess
from app.models import FlightDeal, Passengers, SearchParams

TODAY = date(2026, 9, 30)


def deal(**kw) -> FlightDeal:
    base = dict(origin="Tel Aviv", destination="London", airline="Wizz Air", price=300,
                currency="USD", depart_date="2026-10-14", return_date="2026-10-18", stops=0)
    base.update(kw)
    return FlightDeal(**base)


def test_parse_iso_date():
    assert parse_iso_date("2026-10-05") == date(2026, 10, 5)
    assert parse_iso_date("2026-10-05 to 2026-10-09") == date(2026, 10, 5)
    assert parse_iso_date("mid October") is None
    assert parse_iso_date("2026-02-30") is None


def test_max_stops_filter():
    deals = [deal(stops=0), deal(stops=1, airline="A"), deal(stops=2, airline="B")]
    assert [d.stops for d in postprocess(deals, SearchParams(max_stops=1), TODAY)] == [0, 1]
    assert [d.stops for d in postprocess(deals, SearchParams(max_stops=0), TODAY)] == [0]
    assert len(postprocess(deals, SearchParams(), TODAY)) == 3


def test_max_price_converts_currency():
    params = SearchParams(max_price=300, currency="USD")
    cheap_ils = deal(price=600, currency="ILS")    # ~200 USD at the fallback rate
    pricey_eur = deal(price=400, currency="EUR", airline="El Al")  # ~460 USD
    unknown = deal(price=99999, currency="XYZ", airline="Mystery")  # can't convert -> kept
    out = postprocess([cheap_ils, pricey_eur, unknown], params, TODAY)
    assert [d.airline for d in out] == ["Wizz Air", "Mystery"]


def test_past_and_window_filters():
    params = SearchParams(depart_from=date(2026, 10, 10), depart_to=date(2026, 10, 20))
    deals = [
        deal(depart_date="2026-09-01", return_date=""),                 # past
        deal(depart_date="2026-10-05", return_date="", airline="A"),    # before window
        deal(depart_date="2026-10-15", return_date="", airline="B"),    # inside
        deal(depart_date="2026-10-25", return_date="", airline="C"),    # after window
        deal(depart_date="late October", return_date="", airline="D"),  # unparseable -> kept
    ]
    assert [d.airline for d in postprocess(deals, params, TODAY)] == ["B", "D"]


def test_trip_length_and_type():
    params = SearchParams(nights_min=3, nights_max=5)
    deals = [
        deal(return_date="2026-10-16", airline="2n"),
        deal(return_date="2026-10-18", airline="4n"),
        deal(return_date="2026-10-24", airline="10n"),
        deal(return_date="2026-10-10", airline="backwards"),
    ]
    assert [d.airline for d in postprocess(deals, params, TODAY)] == ["4n"]
    one_way = postprocess([deal(), deal(return_date="", airline="OW")], SearchParams(trip_type="one_way"), TODAY)
    assert [d.airline for d in one_way] == ["OW"]


def test_dedupe_prefers_verified_then_sorts_by_price():
    teaser = deal(price=150, price_confidence="from_price")
    verified = deal(price=180, price_confidence="verified")
    other = deal(price=120, airline="easyJet", depart_date="2026-10-20", return_date="2026-10-24")
    out = postprocess([teaser, verified, other], SearchParams(), TODAY)
    assert [(d.airline, d.price) for d in out] == [("easyJet", 120), ("Wizz Air", 180)]


def test_sort_uses_preferred_currency():
    out = postprocess([deal(price=200, currency="USD"), deal(price=450, currency="ILS", airline="Israir")],
                      SearchParams(currency="USD"), TODAY)
    assert out[0].airline == "Israir"  # 450 ILS ~ 150 USD


def test_price_per_person_and_normalisation():
    params = SearchParams(passengers=Passengers(adults=2, children=1, infants=1))
    out = postprocess([deal(price=900, origin_airport=" tlv", destination_airport="ltn ")], params, TODAY)
    assert out[0].price_per_person == 300  # 3 paying seats; lap infant excluded
    assert (out[0].origin_airport, out[0].destination_airport) == ("TLV", "LTN")
    given = postprocess([deal(price_per_person=280)], params, TODAY)
    assert given[0].price_per_person == 280


def test_drops_nonpositive_prices_and_llm_booking_links():
    d = deal()
    d.booking_links = []
    out = postprocess([deal(price=0), d], SearchParams(), TODAY)
    assert len(out) == 1 and out[0].booking_links == []


def test_flexible_detection_and_party():
    assert is_flexible("anywhere")
    assert is_flexible("Rome, Milan")
    assert is_flexible("Greek islands")
    assert not is_flexible("London")
    assert describe_party(SearchParams()) == "1 adult"
    assert describe_party(SearchParams(passengers=Passengers(adults=2, children=2))) == \
        "2 adults + 2 children (2-11)"


def test_research_prompt_carries_criteria():
    params = SearchParams(trip_type="one_way", cabin_class="business", max_stops=1,
                          passengers=Passengers(adults=2), depart_from=date(2026, 10, 1),
                          depart_to=date(2026, 10, 15), max_price=2000, currency="EUR",
                          nearby_airports=True, checked_bag=True)
    prompt = build_research_prompt("TLV", "London", "early October", params, TODAY)
    for needle in ("Today is 2026-09-30", "one-way", "2 adults", "business class",
                   "at most 1 stop", "between 2026-10-01 and 2026-10-15", "2,000 EUR",
                   "LHR/LGW", "20kg", "Google Flights"):
        assert needle in prompt, needle
    assert "DIRECT" not in prompt
    direct = build_research_prompt("TLV", "Rome", "any", SearchParams(max_stops=0), TODAY)
    assert "direct (non-stop) flights ONLY" in direct


def test_round_trip_search_doubles_one_way_fares():
    params = SearchParams()  # round trip
    out = postprocess([
        deal(price=62, return_date="", price_per_person=62, price_confidence="from_price", airline="noret"),
        deal(price=70, notes="From £70 one-way on Trip.com", airline="ow-note"),
        deal(price=80, airline="rt"),  # has a return date: already a round-trip price
        deal(price=90, return_date="", notes="round-trip fare, return date not shown", airline="rt-note"),
        deal(price=100, notes="one-way £50 doubled for the return leg", airline="done"),
    ], params, TODAY)
    by = {d.airline: d for d in out}
    assert (by["noret"].price, by["noret"].price_per_person, by["noret"].price_confidence) == \
        (124, 124, "estimated")
    assert "2x the one-way fare" in by["noret"].notes
    assert by["ow-note"].price == 140 and by["ow-note"].price_confidence == "estimated"
    assert (by["rt"].price, by["rt-note"].price, by["done"].price) == (80, 90, 100)


def test_one_way_doubling_happens_before_budget_filter():
    out = postprocess([deal(price=200, return_date="")], SearchParams(max_price=300), TODAY)
    assert out == []  # 2 x 200 = 400 is over budget
    one_way = postprocess([deal(price=200, return_date="")], SearchParams(trip_type="one_way"), TODAY)
    assert one_way[0].price == 200  # one-way searches are left alone
