from datetime import date
from urllib.parse import parse_qs, urlparse

import pytest

from app.booking import _parse_date, _parse_month, build_links
from app.models import FlightDeal, Passengers, SearchParams


def make_deal(**kw) -> FlightDeal:
    base = dict(origin="TLV", destination="PRG", airline="Smartwings", price=300, currency="USD",
                depart_date="2026-10-12", return_date="2026-10-16")
    base.update(kw)
    return FlightDeal(**base)


def by_kind(links, kind):
    return [l for l in links if l.kind == kind]


def one(links, kind):
    found = by_kind(links, kind)
    assert len(found) == 1, [l.model_dump() for l in links]
    return found[0]


FAMILY = SearchParams(passengers=Passengers(adults=2, children=1, infants=1),
                      cabin_class="business", max_stops=0, cabin_bag=True, checked_bag=True, currency="EUR")


# ---------------------------------------------------------------- meta-search formats

def test_google_flights_query_round_trip_family():
    link = one(build_links(make_deal(), FAMILY), "google_flights")
    u = urlparse(link.url)
    assert u.netloc == "www.google.com" and u.path == "/travel/flights"
    q = parse_qs(u.query)
    # Google rejects passengers + cabin together, so a party drops the cabin.
    assert q["q"] == ["Flights to PRG from TLV on 2026-10-12 through 2026-10-16 "
                      "for 2 adults 1 child 1 infant nonstop"]
    assert q["curr"] == ["EUR"]
    assert "%20" in link.url  # spaces encoded as %20, not '+'
    assert set(link.prefilled) == {"route", "dates", "passengers", "stops"}


def test_google_flights_solo_traveller_keeps_cabin():
    link = one(build_links(make_deal(), SearchParams(cabin_class="business", max_stops=0)), "google_flights")
    assert parse_qs(urlparse(link.url).query)["q"] == [
        "Flights to PRG from TLV on 2026-10-12 through 2026-10-16 business class nonstop"]
    assert set(link.prefilled) == {"route", "dates", "passengers", "cabin", "stops"}


def test_google_flights_one_way():
    link = one(build_links(make_deal(return_date=""), SearchParams(trip_type="one_way")), "google_flights")
    assert parse_qs(urlparse(link.url).query)["q"] == ["Flights to PRG from TLV on 2026-10-12 oneway"]


def test_skyscanner_round_trip_family():
    link = one(build_links(make_deal(), FAMILY), "skyscanner")
    u = urlparse(link.url)
    assert u.path == "/transport/flights/tlv/prg/261012/261016/"
    q = parse_qs(u.query)
    assert q["adultsv2"] == ["2"]
    assert q["childrenv2"] == ["8|1"]
    assert q["cabinclass"] == ["business"]
    assert q["rtn"] == ["1"]
    assert q["preferdirects"] == ["true"]
    assert "bags" not in link.prefilled and "stops" in link.prefilled


def test_skyscanner_one_way_and_metro_code():
    link = one(build_links(make_deal(destination="London", return_date=""), None), "skyscanner")
    u = urlparse(link.url)
    assert u.path == "/transport/flights/tlv/lond/261012/"
    q = parse_qs(u.query, keep_blank_values=True)
    assert q["rtn"] == ["0"] and q["cabinclass"] == ["economy"] and q["childrenv2"] == [""]


def test_kayak_path_cabin_passengers_bags_stops():
    link = one(build_links(make_deal(), FAMILY), "kayak")
    assert link.url == ("https://www.kayak.com/flights/TLV-PRG/2026-10-12/2026-10-16/business/"
                        "2adults/children-8-1L?sort=price_a&fs=stops=0;cfc=1;bfc=1")
    assert set(link.prefilled) == {"route", "dates", "passengers", "cabin", "stops", "bags"}


def test_kayak_economy_single_adult_no_filters():
    link = one(build_links(make_deal(return_date=""), None), "kayak")
    assert link.url == "https://www.kayak.com/flights/TLV-PRG/2026-10-12/1adults?sort=price_a"
    assert "bags" not in link.prefilled


def test_kayak_only_checked_bag_and_premium():
    p = SearchParams(cabin_class="premium_economy", checked_bag=True)
    link = one(build_links(make_deal(), p), "kayak")
    assert "/premium/1adults?" in link.url and link.url.endswith("&fs=bfc=1")


def test_momondo_mirrors_kayak():
    link = one(build_links(make_deal(), FAMILY), "momondo")
    assert link.url.startswith("https://www.momondo.com/flight-search/TLV-PRG/2026-10-12/2026-10-16/business/2adults/")


# ---------------------------------------------------------------- airline deep links

def test_ryanair_deep_link():
    deal = make_deal(airline="Ryanair", origin_airport="TLV", destination_airport="BGY", destination="Milan")
    link = build_links(deal, FAMILY)[0]
    assert link.kind == "airline" and link.label == "Ryanair"
    q = parse_qs(urlparse(link.url).query, keep_blank_values=True)
    assert urlparse(link.url).path == "/gb/en/trip/flights/select"
    assert q["adults"] == ["2"] and q["children"] == ["1"] and q["infants"] == ["1"] and q["teens"] == ["0"]
    assert q["dateOut"] == ["2026-10-12"] and q["dateIn"] == ["2026-10-16"] and q["isReturn"] == ["true"]
    assert q["originIata"] == ["TLV"] and q["destinationIata"] == ["BGY"]
    assert "cabin" not in link.prefilled  # business requested, Ryanair has none


def test_ryanair_one_way():
    deal = make_deal(airline_code="FR", airline="", return_date="", destination="VIE")
    link = build_links(deal, None)[0]
    q = parse_qs(urlparse(link.url).query, keep_blank_values=True)
    assert q["isReturn"] == ["false"] and q["dateIn"] == [""]
    assert set(link.prefilled) == {"route", "dates", "passengers", "cabin"}


def test_wizz_deep_link_round_trip_and_one_way():
    deal = make_deal(airline="WIZZ AIR", destination_airport="BUD", destination="Budapest")
    link = build_links(deal, FAMILY)[0]
    assert link.url == "https://www.wizzair.com/en-gb/booking/select-flight/TLV/BUD/2026-10-12/2026-10-16/2/1/1/null"
    one_way = build_links(make_deal(airline="Wizz Air", destination="BUD", return_date=""), None)[0]
    assert one_way.url.endswith("/TLV/BUD/2026-10-12/null/1/0/0/null")


def test_easyjet_deep_link():
    deal = make_deal(airline="easyJet Europe", destination_airport="LTN", destination="London")
    link = build_links(deal, FAMILY)[0]
    u = urlparse(link.url)
    assert u.netloc == "www.easyjet.com" and u.path == "/deeplink"
    q = parse_qs(u.query)
    assert q["dep"] == ["TLV"] and q["dest"] == ["LTN"] and q["dd"] == ["2026-10-12"] and q["rd"] == ["2026-10-16"]
    assert q["apax"] == ["2"] and q["cpax"] == ["1"] and q["ipax"] == ["1"] and q["isOneWay"] == ["off"]


def test_airline_matched_by_flight_number():
    link = build_links(make_deal(airline="multiple", flight_numbers="W6 2328 / W6 2327", destination="BUD"), None)[0]
    assert link.label == "Wizz Air"


def test_lcc_needs_real_airport_not_metro_code():
    # "London" resolves to metro LON: fine for meta-search, not for Wizz's engine.
    links = build_links(make_deal(airline="Wizz Air", destination="London"), None)
    assert links[0].kind == "google_flights"
    wizz = one(links, "airline")
    assert wizz.prefilled == [] and wizz.url == "https://www.wizzair.com/en-gb"


def test_airline_without_deep_link_gets_home_page_ranked_last():
    links = build_links(make_deal(airline="El Al Israel Airlines"), FAMILY)
    assert links[0].kind == "google_flights"
    assert links[-1].label == "El Al site" and links[-1].prefilled == []


def test_unknown_airline_gets_meta_search_only():
    links = build_links(make_deal(airline="Air Nowhere"), None)
    assert not by_kind(links, "airline")
    assert [l.kind for l in links] == ["google_flights", "skyscanner", "kayak", "momondo"]


# ---------------------------------------------------------------- agent booking_url

def test_specific_agent_url_kept_but_below_prefilled_airline():
    deal = make_deal(airline="Ryanair", destination="BGY",
                     booking_url="https://www.kiwi.com/en/booking?token=abc")
    links = build_links(deal, None)
    assert links[0].label == "Ryanair" and links[1].kind == "google_flights"
    agent = next(l for l in links if l.kind == "other")
    assert agent.label == "kiwi.com"


def test_generic_agent_homepage_ranked_last():
    links = build_links(make_deal(booking_url="https://www.smartwings.com/"), None)
    assert links[-1].url == "https://www.smartwings.com/"
    assert links[0].kind == "google_flights"


def test_specific_agent_url_on_airline_domain_replaces_home_page():
    deal = make_deal(airline="El Al", booking_url="https://www.elal.com/eng/deals/prague-2026")
    links = build_links(deal, None)
    assert not any(l.url == "https://www.elal.com/eng" for l in links)
    assert any(l.url == deal.booking_url for l in links)


# ---------------------------------------------------------------- dates

def test_unparseable_dates_fall_back_to_month_views():
    links = build_links(make_deal(depart_date="mid October", return_date="late October"), FAMILY)
    g = one(links, "google_flights")
    assert "dates" not in g.prefilled
    assert "in October 2026" in parse_qs(urlparse(g.url).query)["q"][0]
    sky = one(links, "skyscanner")
    assert urlparse(sky.url).path == "/transport/flights/tlv/prg/2610/2610/"
    assert "dates" not in sky.prefilled
    assert not by_kind(links, "kayak")  # Kayak has no month view


def test_round_trip_with_unparseable_return_is_not_dated():
    links = build_links(make_deal(return_date="a week later", airline="Ryanair"), None)
    assert all("dates" not in l.prefilled for l in links)
    assert not any("ryanair.com/gb/en/trip" in l.url for l in links)


def test_date_range_is_not_an_exact_date():
    assert _parse_date("2026-10-12 to 2026-10-15") is None
    assert _parse_date("Oct 12-15") is None


@pytest.mark.parametrize("text,expected", [
    ("2026-10-12", date(2026, 10, 12)),
    ("Mon 2026-10-12", date(2026, 10, 12)),
    ("12 Oct 2026", date(2026, 10, 12)),
    ("Oct 12, 2026", date(2026, 10, 12)),
    ("October 12", date(2026, 10, 12)),
    ("12th October", date(2026, 10, 12)),
    ("Mar 3", date(2027, 3, 3)),  # already past -> next year
    ("2026-02-30", None),
    ("mid October", None),
    ("", None),
])
def test_parse_date(text, expected):
    assert _parse_date(text, today=date(2026, 9, 30)) == expected


def test_parse_month():
    today = date(2026, 9, 30)
    assert _parse_month("mid October", today) == (2026, 10)
    assert _parse_month("early March", today) == (2027, 3)
    assert _parse_month("anytime", today) is None


def test_textual_legacy_dates_still_produce_dated_links():
    links = build_links(make_deal(depart_date="12 Oct 2026", return_date="16 Oct 2026"), None)
    assert one(links, "kayak").url.startswith("https://www.kayak.com/flights/TLV-PRG/2026-10-12/2026-10-16/")


# ---------------------------------------------------------------- legacy / defensive

def test_legacy_params_none_defaults_to_one_adult_economy_round_trip():
    links = build_links(make_deal(), None)
    q = parse_qs(urlparse(one(links, "skyscanner").url).query)
    assert q["adultsv2"] == ["1"] and q["cabinclass"] == ["economy"] and q["rtn"] == ["1"]
    assert "curr" not in parse_qs(urlparse(one(links, "google_flights").url).query)


def test_one_way_params_ignore_stray_return_date():
    link = one(build_links(make_deal(), SearchParams(trip_type="one_way")), "kayak")
    assert link.url.startswith("https://www.kayak.com/flights/TLV-PRG/2026-10-12/1adults")


def test_params_as_dict_accepted():
    links = build_links(make_deal(), {"passengers": {"adults": 3}})
    assert "for 3 adults" in parse_qs(urlparse(one(links, "google_flights").url).query)["q"][0]


def test_city_names_and_embedded_codes_resolve():
    links = build_links(make_deal(origin="Tel Aviv (TLV)", destination="Larnaca"), None)
    assert "/TLV-LCA/" in one(links, "kayak").url


def test_unresolvable_route_skips_route_links():
    links = build_links(make_deal(origin="somewhere nice", destination="Greece, islands",
                                  airline="Air Nowhere"), None)
    assert links == []
    links = build_links(make_deal(origin="somewhere", destination="Atlantis", airline="Air Nowhere",
                                  booking_url="https://example.com/deal/123"), None)
    assert [l.url for l in links] == ["https://example.com/deal/123"]


@pytest.mark.parametrize("garbage", [
    dict(origin="", destination="", depart_date="", return_date=""),
    dict(depart_date="9999-99-99", return_date="2026-10-01"),  # return before depart
    dict(depart_date="Feb 30", return_date="🙂"),
    dict(airline_code="??", flight_numbers="!!!", booking_url="javascript:alert(1)"),
    dict(origin="T", destination="PRAGUE CITY CENTRE, CZECHIA" * 20),
])
def test_never_raises_on_garbage(garbage):
    links = build_links(make_deal(**garbage), None)
    assert isinstance(links, list)
    assert all(l.url.startswith("https://") for l in links)


def test_never_raises_on_broken_model():
    deal = FlightDeal.model_construct(origin=None, destination=123, airline=None, depart_date=None,
                                      return_date=None, booking_url="https://x.example/deal?id=1")
    links = build_links(deal, None)
    assert isinstance(links, list)


def test_no_duplicate_urls():
    links = build_links(make_deal(booking_url="https://www.google.com/travel/flights?q=x"), FAMILY)
    urls = [l.url for l in links]
    assert len(urls) == len(set(urls))


def test_airline_home_page_survives_unresolvable_route():
    links = build_links(make_deal(origin="somewhere", destination="Atlantis"), None)
    assert [l.label for l in links] == ["Smartwings site"]
