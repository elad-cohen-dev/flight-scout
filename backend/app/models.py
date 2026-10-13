"""Pydantic models shared by the agent, storage, and API layers."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.json_schema import SkipJsonSchema

from .currency import normalize as _normalize_currency


TripType = Literal["round_trip", "one_way"]
CabinClass = Literal["economy", "premium_economy", "business", "first"]
PriceConfidence = Literal["verified", "estimated", "from_price"]
BookingLinkKind = Literal["airline", "google_flights", "skyscanner", "kayak", "momondo", "other"]
FareSource = Literal["", "google_flights", "google_explore", "aviasales", "web"]


class Passengers(BaseModel):
    adults: int = Field(default=1, ge=1, le=9, description="Travellers aged 12+")
    children: int = Field(default=0, ge=0, le=8, description="Travellers aged 2-11")
    infants: int = Field(default=0, ge=0, le=4, description="Under 2, on lap")

    @property
    def total(self) -> int:
        return self.adults + self.children + self.infants


class SearchParams(BaseModel):
    """Structured search criteria. They shape the agent's search AND the
    pre-filled booking links, so they are persisted with every stored deal.
    Every field has a default, so old rows/requests without them stay valid."""

    trip_type: TripType = "round_trip"
    passengers: Passengers = Field(default_factory=Passengers)
    cabin_class: CabinClass = "economy"
    max_stops: int | None = Field(default=None, ge=0, le=3, description="None = any, 0 = direct only")
    cabin_bag: bool = Field(default=False, description="Prices must include a 10kg cabin trolley bag")
    checked_bag: bool = Field(default=False, description="Prices must include a 20kg+ checked bag")
    checked_bags: int = Field(
        default=0, ge=0, le=18,
        description="Checked 20kg bags for the whole party; 0 with checked_bag = one per seated traveller (legacy)")
    # Optional structured date window; dates_hint (free text) stays the primary/fallback input.
    depart_from: date | None = Field(default=None, description="Earliest departure date")
    depart_to: date | None = Field(default=None, description="Latest departure date")
    nights_min: int | None = Field(default=None, ge=0, le=60, description="Min trip length (round trip)")
    nights_max: int | None = Field(default=None, ge=0, le=60, description="Max trip length (round trip)")
    max_price: float | None = Field(default=None, gt=0, description="Price ceiling for the whole party, in `currency`")
    currency: str = Field(default="USD", description="Preferred currency for quoted prices")
    nearby_airports: bool = Field(default=False, description="Also consider airports near origin/destination")

    @field_validator("currency")
    @classmethod
    def _iso_currency_param(cls, v: str) -> str:
        return _normalize_currency(v)

    @model_validator(mode="after")
    def _checked_bag_count(self):
        # checked_bags is the count; checked_bag stays as the "any checked bag" flag
        # that older code, labels and stored rows use.
        if self.checked_bags:
            self.checked_bag = True
        elif self.checked_bag:
            self.checked_bags = self.passengers.adults + self.passengers.children
        return self


class BookingLink(BaseModel):
    """A purchase/lookup link for a deal, pre-filled with the search criteria where the site supports it."""

    label: str = Field(description="Button text, e.g. 'Wizz Air', 'Google Flights'")
    url: str
    kind: BookingLinkKind = "other"
    prefilled: list[str] = Field(
        default_factory=list,
        description="Which criteria the URL pre-fills: subset of 'route','dates','passengers','cabin','bags','stops'",
    )


class FlightDeal(BaseModel):
    """A single flight deal found on the web."""

    origin: str = Field(description="Origin city or airport code, e.g. 'TLV'")
    destination: str = Field(description="Destination city or airport code, e.g. 'LON'")
    airline: str = Field(description="Operating airline, or 'multiple' if mixed")
    price: float = Field(description="Total round-trip (or one-way) price as a number")
    currency: str = Field(description="ISO currency code of the price, e.g. 'USD'")
    depart_date: str = Field(description="Departure date or date range, e.g. '2026-09-14' or 'mid September'")
    return_date: str = Field(default="", description="Return date if round trip, empty for one-way")
    duration: str = Field(default="", description="Flight duration, e.g. '5h 20m'")
    stops: int = Field(default=0, description="Number of stops (0 = direct)")
    booking_url: str = Field(default="", description="URL where this fare can be booked or tracked")
    source_url: str = Field(default="", description="URL of the page where the deal was found")
    notes: str = Field(default="", description="Anything relevant: baggage, fare class, deal expiry, caveats")
    # --- richer fields (all optional so older rows stay valid) ---
    origin_airport: str = Field(default="", description="IATA code of the departure airport, e.g. 'TLV'")
    destination_airport: str = Field(default="", description="IATA code of the arrival airport, e.g. 'LTN'")
    depart_time: str = Field(default="", description="Outbound local departure time HH:MM, if known")
    arrive_time: str = Field(default="", description="Outbound local arrival time HH:MM, if known")
    return_depart_time: str = Field(default="", description="Return leg local departure time HH:MM, if known")
    flight_numbers: str = Field(default="", description="e.g. 'W6 2328 / W6 2327'")
    airline_code: str = Field(default="", description="IATA airline code of the main carrier, e.g. 'W6'")
    price_per_person: float | None = Field(default=None, description="Price for one adult incl. requested bags")
    price_confidence: PriceConfidence = Field(
        default="estimated",
        description="verified = exact fare seen for these dates; estimated = computed/approximate; from_price = 'from X' teaser",
    )
    booking_links: list[BookingLink] = Field(default_factory=list, description="Built by booking.build_links, not by the LLM")
    quality_flags: list[str] = Field(
        default_factory=list,
        description="Data-quality issues found by normalize.normalize_deal (see normalize.FLAGS), not by the LLM",
    )
    # Set by code (engine / providers), never by the LLM; '' on legacy rows.
    source: FareSource = Field(
        default="",
        description="Where the fare came from: google_flights (SerpApi, live), google_explore (SerpApi Travel "
                    "Explore: Google's cheapest-dates price, not a confirmed itinerary), aviasales (Travelpayouts "
                    "cache), web (LLM agent). Set by code",
    )
    seen_at: str = Field(
        default="",
        description="ISO time the source observed this price (live = search time; cached = when the cache saw it). Set by code",
    )
    # SerpApi follow-up references (app/details.py): the round trip's return-flight
    # token, the itinerary's booking-options token, and the request they belong to
    # (JSON). Persisted with the row, but kept out of the LLM schema and API output.
    departure_token: SkipJsonSchema[str] = Field(default="", exclude=True)
    booking_token: SkipJsonSchema[str] = Field(default="", exclude=True)
    serp_params: SkipJsonSchema[str] = Field(default="", exclude=True)

    @field_validator("currency")
    @classmethod
    def _iso_currency(cls, v: str) -> str:
        # The extractor sometimes emits a symbol ("₪") instead of an ISO code;
        # normalizing here covers fresh extractions AND old rows read back.
        return _normalize_currency(v)


class PriceInsights(BaseModel):
    """Google Flights' view of a route's price level (SerpApi price_insights),
    in the search currency, for the whole party."""

    route: str = Field(description="e.g. 'TLV>PRG'")
    currency: str
    lowest_price: float | None = None
    price_level: str = Field(default="", description="'low' | 'typical' | 'high' (Google's wording)")
    typical_low: float | None = None
    typical_high: float | None = None
    depart_date: str = ""
    return_date: str = ""
    price_history: list[tuple[int, float]] = Field(
        default_factory=list,
        description="Google's [unix seconds, price] points for THESE dates, oldest first (seeds watch stats)",
    )


class SearchMeta(BaseModel):
    """How a search was answered: which sources ran, Google's price insights,
    and anything the user should know (budget used up, fell back to the web)."""

    sources: list[str] = Field(default_factory=list,
                               description="Sources used, in order: google_flights, google_explore, aviasales, web")
    price_insights: PriceInsights | None = None
    live_searches: int = Field(default=0, description="Paid SerpApi searches this run spent (Explore included)")
    messages: list[str] = Field(default_factory=list)
    decision: str = Field(default="", description="Watch pre-screen decision (watch runs only)")


class ProviderUsage(BaseModel):
    provider: str
    configured: bool
    used: int = Field(description="Calls this calendar month (UTC), cache hits excluded")
    budget: int | None = Field(default=None, description="Monthly cap we enforce; None = uncapped")
    left: int | None = None


class UsageReport(BaseModel):
    month: str = Field(description="YYYY-MM (UTC)")
    providers: list[ProviderUsage]
    watch_projection: int = Field(default=0, description="Live searches the active watches may spend in a month (sum of WatchCredits.per_month)")
    warning: str = Field(default="", description="Set when watch_projection exceeds the monthly live-search budget")
    ai: AISpend | None = Field(default=None, description="Estimated LLM spend this month (app/llm_usage.py)")


class AIModelSpend(BaseModel):
    """One model's LLM usage this month and its estimated cost."""

    provider: str
    model: str
    calls: int = 0
    input_tokens: int = Field(default=0, description="Includes cached input")
    cached_input_tokens: int = 0
    output_tokens: int = 0
    web_searches: int = Field(default=0, description="Paid web-search tool calls")
    web_fetches: int = 0
    cost_usd: float | None = Field(default=None, description="Estimate; None = no price for this model")
    estimated: bool = Field(default=False, description="Price unverified or unknown")


class AISpend(BaseModel):
    """Estimated AI spend this calendar month (UTC): list prices x reported token counts."""

    configured: bool = Field(description="An LLM key is set")
    month: str
    cost_usd: float = 0.0
    searches: int = Field(default=0, description="LLM (web agent) searches run this month")
    web_searches: int = 0
    models: list[AIModelSpend] = Field(default_factory=list)
    estimated: bool = Field(default=False, description="Some model's price is unverified or unknown")
    prices_as_of: str = ""
    budget_usd: float | None = Field(default=None, description="FLIGHT_SCOUT_AI_BUDGET_USD; None = no cap")
    over_budget: bool = False
    warning: str = Field(default="", description="Set at 80% of the budget and when it is used up")
    research_model: str = ""
    extract_model: str = ""


class WatchCredits(BaseModel):
    """What one watch may spend on live searches (engine.watch_credits). An
    upper bound: the pre-screen skips the Google Flights confirmation on most
    days when the price is far from firing."""

    per_check: int = Field(description="Most live searches one check spends")
    per_month: int = Field(description="per_check x the daily sweep's ~30 runs")
    text: str = Field(default="", description="e.g. 'Up to 3 live searches per check (~90/month)'")


class FlightDealList(BaseModel):
    """Structured-output wrapper: the list of deals extracted from research notes."""

    deals: list[FlightDeal] = Field(description="All distinct flight deals found, cheapest first")


BagBasis = Literal["google", "table", "typical", "included", "seller"]
PriceBasis = Literal["none", "as_quoted", "included", "table", "typical", "seller"]


class BagLine(BaseModel):
    """One kind of bag in a deal's price with bags, for the whole party and trip."""

    kind: Literal["trolley", "checked"]
    count: int = Field(description="Bags per direction, whole party")
    directions: int = Field(description="1 one-way, 2 round trip")
    low: float = Field(description="Fee range low end, in the deal currency (all bags, all directions)")
    high: float
    basis: BagBasis = Field(description="google = already in Google's fare (bags filter); seller = the seller's own "
                                        "fee from booking options; table = app/data/bag_fees.json; typical = no entry "
                                        "for this airline, a typical low-cost range; included = the fare includes it")
    carrier: str = Field(default="", description="Airline name the fee is for")


class BagPricing(BaseModel):
    """A deal's price with the requested bags (app/bags.py), computed on read so
    a bag-fee table update re-prices every stored deal."""

    basis: PriceBasis = Field(description="Weakest basis of the bag lines; none = no bags asked; as_quoted = the "
                                          "source's price already claims to include bags (LLM web results)")
    fare: float = Field(description="The deal's own price")
    total: float = Field(description="Best estimate: fare + the middle of the bag-fee range")
    total_min: float
    total_max: float
    currency: str
    lines: list[BagLine] = Field(default_factory=list)
    added_at_checkout: bool = Field(default=False, description="Bags are bought at checkout, so the site's first price is lower")
    as_of: str = Field(default="", description="Month the fee table was checked, e.g. '2026-10'")


class StoredDeal(FlightDeal):
    """A deal as returned from the database."""

    id: int
    search_query: str
    found_at: datetime
    times_seen: int = Field(default=1, description="How many search runs found this exact deal")
    first_found_at: datetime | None = Field(default=None, description="When this deal was first seen (set when times_seen > 1)")
    search_params: SearchParams | None = Field(default=None, description="Criteria of the run that found it (None for legacy rows)")
    compare_price: float | None = Field(
        default=None,
        description="price converted into compare_currency, for sorting/'cheapest' across source currencies; None if unconvertible",
    )
    compare_currency: str = Field(default="USD", description="The run's preferred currency (USD for legacy rows)")
    route_key: str = Field(default="", description="Canonical route id, e.g. 'TLV>PRG' — groups 'Prague', 'PRG' and 'Prague [PRG]'")
    details_cost: int = Field(
        default=0,
        description="Most live searches 'Details & book direct' may spend (1 one-way, 2 round trip); 0 = not available",
    )
    bags: BagPricing | None = Field(default=None, description="Price with the requested bags (app/bags.py)")
    compare_total: float | None = Field(
        default=None, description="bags.total (else price) in compare_currency — what the UI sorts by")


class HistoryPoint(BaseModel):
    """Best price found for a route in one search run."""

    found_at: datetime
    price: float
    deals: int = Field(description="How many deals that run found for the route")


class RouteHistory(BaseModel):
    route: str = Field(description="Requested route, e.g. 'TLV -> London'")
    route_key: str = Field(default="", description="Canonical route id matching StoredDeal.route_key")
    currency: str
    points: list[HistoryPoint] = Field(description="Chronological, oldest first")


AlertRule = Literal["threshold", "new_low", "under_typical", "big_drop"]


class AlertSettings(BaseModel):
    """When a watch alerts, beyond (or instead of) the absolute max_price.

    Every rule compares the run's best price against this watch's earlier runs
    (same party/cabin/trip type, converted into the watch currency) of one trust
    tier, or Google's price insights while it has few verified runs; see
    app/alerts.py. Defaults are on, so existing watches get the smart rules."""

    threshold: bool = Field(default=True, description="Alert when the best price is at or under max_price")
    new_low: bool = Field(default=True, description="Alert on the lowest price in `new_low_days`")
    new_low_days: int = Field(default=30, ge=7, le=365)
    under_typical: bool = Field(default=True, description="Alert when under the median of recent runs by `under_typical_pct`")
    under_typical_pct: float = Field(default=20, ge=5, le=80)
    big_drop: bool = Field(default=True, description="Alert when the best price fell `big_drop_pct` vs the previous run")
    big_drop_pct: float = Field(default=15, ge=5, le=80)


StatsBasis = Literal["verified", "google", "explore", "cached", "legacy"]


class LegacyStats(BaseModel):
    """Numbers from unverified (pre-2.0 / LLM web) runs, shown muted while the
    trusted basis is still thin. They never feed the score or the rules then."""

    runs: int
    low_30d: float | None = None
    typical: float | None = None
    all_time_low: float | None = None


class WatchStats(BaseModel):
    """Price statistics of a watch's runs, in the watch currency (read-only).

    Only one trust tier feeds the numbers — see `basis` and app/alerts.py
    (select_baseline) for the rule."""

    currency: str
    runs: int = Field(description="Runs with a comparable price (every tier)")
    last_best: float | None = Field(default=None, description="Latest run-best of the basis tier")
    last_run_at: datetime | None = None
    low_30d: float | None = None
    typical: float | None = Field(default=None, description="Median of the basis' last 30 days, or the middle of Google's typical range")
    all_time_low: float | None = None
    score: int | None = Field(default=None, description="0-100 deal score of the last best price vs the basis")
    basis: StatsBasis = Field(
        default="legacy",
        description="verified = own Google Flights runs; google = Google's price insights (+ own verified runs); "
                    "explore = Google Travel Explore prices only; cached = Aviasales cache runs; "
                    "legacy = unverified LLM-era estimates only",
    )
    basis_runs: int = Field(default=0, description="Runs of the basis tier (verified ones for basis=google)")
    history_points: int = Field(default=0, description="Google price-history points of the last 30 days used")
    typical_low: float | None = Field(default=None, description="Google's typical range (basis=google)")
    typical_high: float | None = None
    legacy_runs: int = Field(default=0, description="Unverified runs left out of the numbers")
    legacy: LegacyStats | None = Field(default=None, description="Their numbers, while the basis is thin")


class AlertRecord(BaseModel):
    """One sent alert (row of the alert_log table)."""

    watch_id: int
    fingerprint: str
    price: float
    currency: str
    rule: str = Field(description="Comma-separated AlertRule names that fired")
    score: int | None = None
    sent_at: datetime


class WatchCreate(SearchParams):
    """A route the user wants monitored for price drops."""

    origin: str
    destination: str
    dates_hint: str = "anytime in the next 3 months"
    max_price: float = Field(gt=0, description="Alert when a deal is at or below this price")
    currency: str = Field(default="USD", description="Currency the threshold is expressed in")
    direct_only: bool = Field(default=False, description="Legacy alias for max_stops=0")
    alerts: AlertSettings = Field(default_factory=AlertSettings, description="Which alert rules apply")

    @model_validator(mode="after")
    def _direct_alias(self):
        if self.direct_only and self.max_stops is None:
            self.max_stops = 0
        self.direct_only = self.max_stops == 0
        return self


class Watch(WatchCreate):
    id: int
    active: bool = True
    created_at: datetime
    last_run_at: datetime | None = None
    last_best_price: float | None = None
    # Computed on read by the API (alerts.enrich_watches); never stored.
    stats: WatchStats | None = None
    last_alert: AlertRecord | None = None
    credits: WatchCredits | None = None


# WatchUpdate fields where an explicit null means something ("any stops", "no
# date window"); for every other field null is rejected rather than ignored.
_NULLABLE_UPDATE_FIELDS = {"max_stops", "depart_from", "depart_to", "nights_min", "nights_max"}


class WatchUpdate(BaseModel):
    """Partial update for PATCH /api/watches/{id}: only the fields sent change.

    The route (origin/destination) is the watch's identity and can't be edited —
    delete and re-create instead. Types are checked here; ranges and currency
    normalization are enforced by re-validating the merged result as a
    WatchCreate (see apply_to)."""

    model_config = ConfigDict(extra="forbid")

    dates_hint: str | None = None
    max_price: float | None = None
    currency: str | None = None
    direct_only: bool | None = Field(default=None, description="Legacy alias: true = max_stops 0, false clears a direct-only filter")
    active: bool | None = Field(default=None, description="false pauses the watch (skipped by the daily run), true resumes it")
    trip_type: TripType | None = None
    passengers: Passengers | None = None
    cabin_class: CabinClass | None = None
    max_stops: int | None = None
    cabin_bag: bool | None = None
    checked_bag: bool | None = None
    checked_bags: int | None = None
    depart_from: date | None = None
    depart_to: date | None = None
    nights_min: int | None = None
    nights_max: int | None = None
    nearby_airports: bool | None = None
    alerts: AlertSettings | None = Field(default=None, description="Partial: only the rule fields sent change")

    @model_validator(mode="after")
    def _no_null_for_required(self):
        nulled = sorted(f for f in self.model_fields_set - _NULLABLE_UPDATE_FIELDS if getattr(self, f) is None)
        if nulled:
            raise ValueError(f"cannot be null: {', '.join(nulled)}")
        return self

    def apply_to(self, watch: Watch) -> tuple[WatchCreate, bool]:
        """The watch's criteria with this patch applied, re-validated, plus its active flag.
        Raises pydantic.ValidationError when the merged watch is invalid."""
        patch = self.model_dump(exclude_unset=True)
        active = patch.pop("active", watch.active)
        merged = watch.model_dump(include=set(WatchCreate.model_fields))
        if "alerts" in patch:  # exclude_unset is recursive: merge just the rule fields sent
            patch["alerts"] = {**merged["alerts"], **patch["alerts"]}
        merged.update(patch)
        if patch.get("checked_bag") is False and "checked_bags" not in patch:
            merged["checked_bags"] = 0  # else the stored count would switch the flag back on
        if "max_stops" not in patch and "direct_only" in patch:
            if patch["direct_only"]:
                merged["max_stops"] = 0
            elif merged["max_stops"] == 0:
                merged["max_stops"] = None
        # Recompute the alias from max_stops, else a stale direct_only=True would
        # force max_stops back to 0 when the patch widens it to "any".
        merged["direct_only"] = merged["max_stops"] == 0
        return WatchCreate.model_validate(merged), active


def _criteria_flags(p: SearchParams) -> list[str]:
    """Human-readable non-default criteria, in label order. Each flag is one
    comma-free token so labels stay splittable on ', '."""
    flags: list[str] = []
    if p.depart_from and p.depart_to:
        flags.append(f"depart {p.depart_from.isoformat()} to {p.depart_to.isoformat()}")
    elif p.depart_from:
        flags.append(f"depart from {p.depart_from.isoformat()}")
    elif p.depart_to:
        flags.append(f"depart by {p.depart_to.isoformat()}")
    if p.trip_type == "round_trip" and (p.nights_min is not None or p.nights_max is not None):
        lo, hi = p.nights_min, p.nights_max
        if lo is not None and hi is not None:
            flags.append(f"{lo} nights" if lo == hi else f"{lo}-{hi} nights")
        elif lo is not None:
            flags.append(f"{lo}+ nights")
        else:
            flags.append(f"up to {hi} nights")
    if p.trip_type == "one_way":
        flags.append("one-way")
    pax = p.passengers
    if (pax.adults, pax.children, pax.infants) != (1, 0, 0):
        party = [f"{pax.adults} adult{'s' if pax.adults != 1 else ''}"]
        if pax.children:
            party.append(f"{pax.children} child{'ren' if pax.children != 1 else ''}")
        if pax.infants:
            party.append(f"{pax.infants} infant{'s' if pax.infants != 1 else ''}")
        flags.append(" + ".join(party))
    if p.cabin_class != "economy":
        flags.append(p.cabin_class.replace("_", " "))
    if p.max_stops is not None and p.max_stops > 0:
        flags.append(f"max {p.max_stops} stop{'s' if p.max_stops != 1 else ''}")
    if p.checked_bags and p.checked_bags != pax.adults + pax.children:
        flags.append(f"{p.checked_bags} checked bag{'s' if p.checked_bags != 1 else ''}")
    if p.nearby_airports:
        flags.append("nearby airports")
    if p.max_price is not None:
        flags.append(f"up to {p.max_price:.0f} {p.currency}")
    return flags


def search_label(origin: str, destination: str, dates_hint: str,
                 direct_only: bool, cabin_bag: bool, checked_bag: bool,
                 params: SearchParams | None = None) -> str:
    """Canonical stored label for a search run, e.g.
    'TLV -> Prague (October, direct only, cabin bag, checked 20kg bag)', or with
    params: 'TLV -> Prague (October, one-way, 2 adults + 1 child, business,
    max 1 stop, cabin bag)'.

    The flags live inside the parens so route parsing (split on ' (') and the
    frontend's re-run parser stay in sync. The legacy flags (direct only, cabin
    bag, checked 20kg bag) always come last and in that order, so old parsers
    that peel them off the tail keep working; newer criteria sit between the
    dates text and those. `params`, when given, supersedes the three flags."""
    flags = ""
    if params is not None:
        direct_only = params.max_stops == 0
        cabin_bag, checked_bag = params.cabin_bag, params.checked_bag
        for flag in _criteria_flags(params):
            flags += f", {flag}"
    if direct_only:
        flags += ", direct only"
    if cabin_bag:
        flags += ", cabin bag"
    if checked_bag:
        flags += ", checked 20kg bag"
    return f"{origin} -> {destination} ({dates_hint}{flags})"


class SearchRequest(SearchParams):
    origin: str = Field(description="City, airport code, or comma-separated list")
    destination: str = Field(description="City, airport, country/region ('Greece'), comma list, or 'anywhere'")
    dates_hint: str = Field(
        default="anytime in the next 3 months",
        description="Free-text date preference, e.g. 'first two weeks of October', 'a weekend in November'",
    )
    direct_only: bool = Field(default=False, description="Legacy alias for max_stops=0")

    @model_validator(mode="after")
    def _direct_alias(self):
        if self.direct_only and self.max_stops is None:
            self.max_stops = 0
        self.direct_only = self.max_stops == 0
        return self

    def params(self) -> SearchParams:
        return SearchParams(**self.model_dump(include=set(SearchParams.model_fields)))
