from datetime import date

from app.models import Passengers, SearchParams, SearchRequest, search_label


def test_legacy_format_unchanged():
    assert search_label("TLV", "Prague", "October", True, True, True) == \
        "TLV -> Prague (October, direct only, cabin bag, checked 20kg bag)"
    assert search_label("TLV", "Prague", "October", False, False, False) == "TLV -> Prague (October)"


def test_default_params_match_legacy_label():
    assert search_label("TLV", "Rome", "May", False, False, False, params=SearchParams()) == \
        "TLV -> Rome (May)"
    req = SearchRequest(origin="TLV", destination="Rome", dates_hint="May", direct_only=True, cabin_bag=True)
    assert search_label(req.origin, req.destination, req.dates_hint, req.direct_only,
                        req.cabin_bag, req.checked_bag, params=req.params()) == \
        "TLV -> Rome (May, direct only, cabin bag)"


def test_rich_label_keeps_legacy_flags_last():
    params = SearchParams(trip_type="one_way", passengers=Passengers(adults=2, children=1),
                          cabin_class="premium_economy", max_stops=1, nearby_airports=True,
                          max_price=1500, currency="EUR", checked_bag=True)
    label = search_label("TLV", "London", "October", False, False, False, params=params)
    assert label == ("TLV -> London (October, one-way, 2 adults + 1 child, premium economy, "
                     "max 1 stop, nearby airports, up to 1500 EUR, checked 20kg bag)")
    assert label.split(" (")[0] == "TLV -> London"  # route grouping for history


def test_window_and_nights_flags():
    params = SearchParams(depart_from=date(2026, 10, 1), depart_to=date(2026, 10, 20),
                          nights_min=3, nights_max=6, max_stops=0)
    assert search_label("TLV", "Rome", "October", False, False, False, params=params) == \
        "TLV -> Rome (October, depart 2026-10-01 to 2026-10-20, 3-6 nights, direct only)"
