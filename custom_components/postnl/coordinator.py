from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import requests
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import (DataUpdateCoordinator,
                                                      UpdateFailed)

from .auth import AsyncConfigEntryAuth
from .const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    CONF_INCLUDE_HISTORY,
    CONF_REFRESH_INTERVAL,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    DEFAULT_INCLUDE_HISTORY,
    DEFAULT_REFRESH_INTERVAL,
    DOMAIN,
    HISTORY_MAX_EVENTS,
    ParcelStatus,
)
from .graphql import PostNLGraphql
from .jouw_api import PostNLJouwAPI

_LOGGER = logging.getLogger(__name__)


# PostNL's status comes from two signals:
#   - shipment.delivered (bool, GraphQL) — terminal indicator
#   - colli.statusPhase.message (Track & Trace) — Dutch human-readable string
#
# statusPhase.message is not under an API contract: PostNL changes the wording
# at will. So we substring-match against the meaningful subphrase rather than
# look up against a fixed enum. Order matters — first match wins, so the more
# specific patterns must come before the broader ones (e.g. "wordt vandaag
# bezorgd" before "bezorgd").
_STATUS_PATTERNS: tuple[tuple[str, ParcelStatus], ...] = (
    ("ligt klaar bij postnl punt", ParcelStatus.AT_PICKUP_POINT),
    ("afgeleverd op postnl punt", ParcelStatus.AT_PICKUP_POINT),
    ("klaar bij postnl punt", ParcelStatus.AT_PICKUP_POINT),
    ("teruggestuurd", ParcelStatus.RETURNING),
    ("retour", ParcelStatus.RETURNING),
    ("wordt vandaag bezorgd", ParcelStatus.OUT_FOR_DELIVERY),
    ("onderweg naar het bezorgadres", ParcelStatus.OUT_FOR_DELIVERY),
    ("onderweg naar de bezorger", ParcelStatus.OUT_FOR_DELIVERY),
    ("aangemeld", ParcelStatus.REGISTERED),
    ("verwacht", ParcelStatus.REGISTERED),
    ("bezorgmoment is bijgewerkt", ParcelStatus.IN_TRANSIT),
    ("lukt vandaag niet", ParcelStatus.IN_TRANSIT),
    ("ontvangen", ParcelStatus.IN_TRANSIT),
    ("gesorteerd", ParcelStatus.IN_TRANSIT),
    ("onderweg", ParcelStatus.IN_TRANSIT),
    ("bezorgd", ParcelStatus.DELIVERED),
    ("unknown", ParcelStatus.UNKNOWN),
)

# History events carry a stable PostNL ``observationCode`` (e.g. ``J05``)
# rather than the brittle human text, so the timeline maps from the code.
# Unmapped codes resolve to ``None`` (+ a one-shot warning, see feature B).
# Milestone codes — events that represent a real movement stage. These drive
# the history ``status`` and progress monotonically (registered → in_transit →
# out_for_delivery → delivered), with the one legitimate exception of a real
# delivery delay/failure (G/T codes) dropping back to in_transit.
_OBSERVATION_CODE_MAP: dict[str, ParcelStatus] = {
    # --- Pre-receipt baseline (registered) ---
    "A01": ParcelStatus.REGISTERED,       # nog niet ontvangen/verwerkt
    "A03": ParcelStatus.REGISTERED,       # zending aangemeld (pre-advice)
    "M02": ParcelStatus.REGISTERED,       # nog niet ontvangen/verwerkt (variant of A01)
    # --- In the network (in_transit) ---
    "B01": ParcelStatus.IN_TRANSIT,       # ontvangen door PostNL
    "J01": ParcelStatus.IN_TRANSIT,       # gesorteerd
    "R01": ParcelStatus.IN_TRANSIT,       # zending is gesorteerd (variant of J01)
    "J04": ParcelStatus.IN_TRANSIT,       # voorgemeld en gescand op rit
    "J21": ParcelStatus.IN_TRANSIT,       # overgedragen aan afhaallocatie (handover, not yet ready)
    "J31": ParcelStatus.IN_TRANSIT,       # ingenomen aan de sorteergoot
    "J32": ParcelStatus.IN_TRANSIT,       # overgedragen tijdens route
    "J40": ParcelStatus.IN_TRANSIT,       # voorgemeld en gesorteerd op rit
    "J44": ParcelStatus.IN_TRANSIT,       # overgenomen tijdens route
    "J55": ParcelStatus.IN_TRANSIT,       # verwacht bij PostNL-punt
    # --- Real delivery delay / failed attempt: a genuine step back to transit ---
    "G01": ParcelStatus.IN_TRANSIT,       # bezorgmoment bijgewerkt — lukt vandaag niet
    "G05": ParcelStatus.IN_TRANSIT,       # bezorgmoment bijgewerkt (delay)
    "T04": ParcelStatus.IN_TRANSIT,       # bezorgmoment bijgewerkt (delay)
    # --- Out for delivery ---
    "J05": ParcelStatus.OUT_FOR_DELIVERY, # bezorger is onderweg
    # --- At a pickup point ---
    "I08": ParcelStatus.AT_PICKUP_POINT,  # zending beschikbaar op PostNL-punt
    "J02": ParcelStatus.AT_PICKUP_POINT,  # ligt klaar bij PostNL-punt
    "J12": ParcelStatus.AT_PICKUP_POINT,  # ligt klaar bij PostNL-punt
    # --- Delivered / collected (terminal) ---
    "A80": ParcelStatus.DELIVERED,        # handtekening voor pakket ontvangen (proof of delivery)
    "I01": ParcelStatus.DELIVERED,        # pakket is bezorgd
    "I02": ParcelStatus.DELIVERED,        # afgehaald bij PostNL-punt
    "I05": ParcelStatus.DELIVERED,        # bezorgd in de brievenbus
}

# Known notification / admin / data events that are NOT a movement milestone.
# PostNL interleaves these throughout the timeline (often out of order), so
# mapping them to a movement status makes the history bounce back and forth
# (e.g. an "ETA gewijzigd" right after "Bezorger is onderweg" would otherwise
# drag the status from out_for_delivery back to in_transit). They resolve to
# ``status: null`` — known, so no "unrecognised" warning — while the exact
# label is preserved on ``raw_status``.
_OBSERVATION_META_CODES: frozenset[str] = frozenset({
    "A04",  # PIOS melding (notification)
    "A18",  # ETA initieel bepaald
    "A19",  # ETA gewijzigd
    "A25",  # reminderservice notificaties
    "A65",  # zending herpland (planning change)
    "A94",  # zelf bezorging gewijzigd
    "A95",  # bezorging wijzigen niet mogelijk
    "A96",  # bezorging wijzigen mogelijk
    "A98",  # voorgemelde zending verrijkt door PostNL regie (data enrichment)
    "K33",  # "leeg" placeholder
    "K50",  # RCS melding (notification)
})

# New-issue link surfaced in the unknown-status warnings so users can paste a
# ready-made line into a bug report.
_NEW_ISSUE_URL = "https://github.com/ha-parcel-integrations/ha-postnl/issues/new"

# One-shot dedupe sets so each distinct unmapped value warns once per HA
# session rather than on every poll.
_LOGGED_UNKNOWN_STATUSES: set[str] = set()
_LOGGED_UNKNOWN_OBSERVATION_CODES: set[str] = set()


def _refresh_interval(entry: ConfigEntry) -> timedelta:
    """Return the configured refresh interval from the config entry options."""
    minutes = int(
        entry.options.get(CONF_REFRESH_INTERVAL, DEFAULT_REFRESH_INTERVAL)
    )
    return timedelta(minutes=minutes)


def map_parcel_status(parcel: dict) -> ParcelStatus:
    """Map a PostNL parcel dict to a canonical :class:`ParcelStatus`.

    ``parcel`` is the intermediate dict built by :meth:`transform_shipment`
    with at least ``delivered`` (bool) and ``status_message`` (str) populated.
    The ``delivered`` flag from GraphQL is authoritative and short-circuits to
    :attr:`ParcelStatus.DELIVERED`. Anything that does not match any pattern
    is reported as :attr:`ParcelStatus.UNKNOWN` and surfaced once at info
    level so users can open an issue to extend the map.
    """
    if parcel.get("delivered"):
        return ParcelStatus.DELIVERED

    raw = (parcel.get("status_message") or "").strip().lower()
    if not raw:
        return ParcelStatus.UNKNOWN

    for pattern, status in _STATUS_PATTERNS:
        if pattern in raw:
            return status

    if raw not in _LOGGED_UNKNOWN_STATUSES:
        _LOGGED_UNKNOWN_STATUSES.add(raw)
        _LOGGER.warning(
            "Unrecognised PostNL status — help us map it. Open an issue and "
            "paste this line: %s\n  [parcel] statusPhase.message=%r "
            "→ reported as 'unknown'",
            _NEW_ISSUE_URL,
            parcel.get("status_message"),
        )
    return ParcelStatus.UNKNOWN


def map_observation_status(
    code: str | None, description: str | None = None
) -> ParcelStatus | None:
    """Map a Track & Trace ``observationCode`` to a canonical milestone status.

    Returns a :class:`ParcelStatus` only for genuine movement milestones.
    Known notification/admin codes (:data:`_OBSERVATION_META_CODES`) and
    unmapped codes both return ``None``; meta codes do so silently, while an
    unmapped code surfaces a one-shot warning with copy-paste issue text so
    users can help extend the map. :func:`build_history` decides what a
    ``None`` becomes (carry the previous stage forward for meta, leave null
    for unknown).
    """
    if not code:
        return None
    status = _OBSERVATION_CODE_MAP.get(code)
    if status is not None:
        return status

    if code in _OBSERVATION_META_CODES:
        return None  # known notification/admin event — no own movement status

    if code not in _LOGGED_UNKNOWN_OBSERVATION_CODES:
        _LOGGED_UNKNOWN_OBSERVATION_CODES.add(code)
        _LOGGER.warning(
            "Unrecognised PostNL status — help us map it. Open an issue and "
            "paste this line: %s\n  [history] observationCode=%s text=%r "
            "→ reported as 'unknown'",
            _NEW_ISSUE_URL,
            code,
            description,
        )
    return None


def _extract_observations(colli: dict) -> list[dict]:
    """Return the status-event list from a colli object, oldest-first preferred.

    ``analyticsInfo.allObservations`` carries the **complete** timeline
    (oldest-first); the top-level ``observations`` list is truncated to the
    most recent few. Prefer the former, fall back to the latter.
    """
    analytics = colli.get("analyticsInfo") or {}
    return analytics.get("allObservations") or colli.get("observations") or []


def build_history(
    observations: list[dict] | None, *, max_events: int = HISTORY_MAX_EVENTS
) -> list[dict]:
    """Build the canonical ``history`` list from raw Track & Trace observations.

    Each entry is ``{timestamp, status, raw_status}`` — identical across all
    suite carriers. Sorted oldest → newest by timestamp (entries with an
    unparseable timestamp keep their original order, after the parseable ones)
    and capped to the most recent ``max_events``.

    PostNL interleaves notification/admin events (ETA recalcs, "bezorging
    wijzigen mogelijk", data enrichment, …) throughout the timeline, often out
    of order. Those carry **no movement status of their own**; instead they
    inherit the stage of the most recent milestone (carry-forward), so the
    timeline never bounces backwards on a cosmetic event — e.g. an "ETA
    gewijzigd" logged the moment the courier sets off reads ``out_for_delivery``,
    while the same code earlier (just after sorting) reads ``in_transit``.
    Genuine milestones drive the status and only a real delivery delay (G/T)
    steps back to ``in_transit``. Unmapped codes stay ``null`` (+ warning).
    """
    parseable: list[tuple[datetime, tuple]] = []
    unparseable: list[tuple] = []
    for obs in observations or []:
        timestamp = obs.get("observationDate")
        if not timestamp:
            continue
        record = (timestamp, obs.get("observationCode"), obs.get("description"))
        dt = _parse_iso(timestamp)
        if dt is None:
            unparseable.append(record)
        else:
            parseable.append((dt, record))
    parseable.sort(key=lambda item: item[0])
    ordered = [record for _, record in parseable] + unparseable

    history: list[dict] = []
    # A parcel that appears in track-trace has, at minimum, been pre-announced,
    # so the implicit baseline stage is REGISTERED. This is what a meta event
    # (e.g. "Voorgemelde zending verrijkt") inherits when it lands before the
    # first real milestone, instead of showing a bare null.
    last_status: ParcelStatus | None = ParcelStatus.REGISTERED
    for timestamp, code, description in ordered:
        status = map_observation_status(code, description)
        if status is not None:
            last_status = status
        elif code in _OBSERVATION_META_CODES:
            # Known notification/admin event — show the stage the parcel was
            # already in rather than inventing a (backwards) movement.
            status = last_status
        # else: unmapped → leave None (map_observation_status already warned)
        history.append(
            {"timestamp": timestamp, "status": status, "raw_status": description}
        )
    return history[-max_events:]


def _convert_native_dimensions(
    native: dict | None,
) -> tuple[float | None, dict | None]:
    """Convert PostNL's native dimensions (g + mm) to the suite-wide canonical
    shape (kg + cm with a pre-formatted ``text`` string).

    PostNL ships ``{height, width, depth, weight}`` in grams + millimetres,
    and calls the long edge ``depth`` rather than ``length``. The canonical
    contract every other carrier in the suite honours is kg + cm + ``length``,
    with a pre-formatted ``"L x W x H cm"`` string under ``text`` so dashboard
    cards can render it directly.
    """
    if not native:
        return None, None
    weight_g = native.get("weight")
    weight_kg = weight_g / 1000 if weight_g is not None else None
    depth_mm = native.get("depth")
    width_mm = native.get("width")
    height_mm = native.get("height")
    if depth_mm is None or width_mm is None or height_mm is None:
        return weight_kg, None
    length_cm = depth_mm / 10
    width_cm = width_mm / 10
    height_cm = height_mm / 10
    text = f"{int(round(length_cm))} x {int(round(width_cm))} x {int(round(height_cm))} cm"
    return weight_kg, {
        "length": length_cm,
        "width": width_cm,
        "height": height_cm,
        "text": text,
    }


def normalize_parcel(parcel: dict, *, history: list[dict] | None = None) -> dict:
    """Wrap a transformed PostNL parcel in the canonical carrier-agnostic shape.

    Top-level keys mirror DHL / DPD / parcel-aggregator. The original
    transformed PostNL payload (GraphQL + Track & Trace fields like
    ``status_message``, ``delivery_address_type``, ``planned_date``,
    ``expected_datetime`` and the native ``dimensions`` dict in g + mm)
    is preserved under ``raw`` for power users.

    ``history`` is the optional per-parcel status timeline (opt-in option,
    default off → ``None``). It stays top-level so it survives the
    aggregator's ``strip_raw()`` and flows through unchanged.
    """
    delivered = bool(parcel.get("delivered"))
    weight_kg, canonical_dimensions = _convert_native_dimensions(
        parcel.get("dimensions")
    )
    return {
        "carrier": "PostNL",
        "barcode": parcel.get("barcode"),
        "sender": parcel.get("source_display_name"),
        "receiver": parcel.get("receiver"),
        "status": map_parcel_status(parcel),
        "raw_status": parcel.get("status_message"),
        "delivered": delivered,
        "delivered_at": parcel.get("delivery_date") if delivered else None,
        "planned_from": None if delivered else parcel.get("planned_from"),
        "planned_to": None if delivered else parcel.get("planned_to"),
        "pickup": parcel.get("delivery_address_type") == "ServicePoint",
        "pickup_point": None,
        "url": parcel.get("url"),
        "weight": weight_kg,
        "dimensions": canonical_dimensions,
        "history": history,
        "raw": parcel,
    }


def _parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO 8601 string to an aware datetime, or ``None`` on failure.

    PostNL mixes offset-aware and naive timestamps in the same payload; naive
    values are treated as UTC so a mixed list still sorts without crashing.
    """
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _delivery_dt(parcel: dict) -> datetime | None:
    """Parse the delivery datetime from a normalised parcel dict."""
    return _parse_iso(parcel.get("delivered_at"))


def sort_parcels_by_ts(
    parcels: list[dict], key_field: str, *, descending: bool = False
) -> list[dict]:
    """Return normalised parcels sorted by the ISO timestamp at ``key_field``.

    Parcels whose value is missing or unparseable always sort to the end,
    regardless of ``descending`` — so freshly registered parcels without
    an ETA stay visible at the bottom instead of jumping to the top.
    """
    with_ts: list[tuple[datetime, dict]] = []
    without_ts: list[dict] = []
    for parcel in parcels:
        value = parcel.get(key_field)
        if not isinstance(value, str) or not value:
            without_ts.append(parcel)
            continue
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            without_ts.append(parcel)
            continue
        # PostNL mixes offset-aware and naive timestamps in the same payload;
        # treat naive values as UTC so the sort doesn't crash on a mixed list.
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        with_ts.append((dt, parcel))
    with_ts.sort(key=lambda item: item[0], reverse=descending)
    return [p for _, p in with_ts] + without_ts


_DUTCH_MONTHS = {
    "januari": 1, "februari": 2, "maart": 3, "april": 4,
    "mei": 5, "juni": 6, "juli": 7, "augustus": 8,
    "september": 9, "oktober": 10, "november": 11, "december": 12,
}


def parse_letter_date(title: str, *, today: datetime | None = None) -> str | None:
    """Convert a Dutch day-month title like '16 juni' into an ISO date string.

    The MyMail endpoint returns dates without a year. We infer the year from
    ``today``: if the parsed month/day is more than 31 days ahead of today, it
    must belong to the previous year (PostNL only retains 2 weeks of mail).
    """
    if not title:
        return None
    parts = title.strip().lower().split()
    if len(parts) != 2:
        return None
    try:
        day = int(parts[0])
    except ValueError:
        return None
    month = _DUTCH_MONTHS.get(parts[1])
    if month is None:
        return None
    now = (today or datetime.now(timezone.utc)).date()
    try:
        candidate = now.replace(month=month, day=day)
    except ValueError:
        return None
    if (candidate - now).days > 31:
        try:
            candidate = candidate.replace(year=candidate.year - 1)
        except ValueError:
            return None
    return candidate.isoformat()


def extract_letters(payload: dict, *, today: datetime | None = None) -> list[dict]:
    """Extract letter entries from the server-driven-UI MyMail response."""
    sections = ((payload or {}).get("screen") or {}).get("sections") or []
    letters: list[dict] = []
    for section in sections:
        for item in section.get("items") or []:
            if item.get("type") != "Letter":
                continue
            title = item.get("title")
            letters.append({
                "id": item.get("editId"),
                "title": title,
                "date": parse_letter_date(title, today=today),
                "unread": bool(item.get("isUnread")),
                "image_url": (item.get("image") or {}).get("url"),
            })
    return letters


class PostNLCoordinator(DataUpdateCoordinator):
    data: dict[str, list[dict]]

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize PostNL coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name="PostNL",
            update_interval=_refresh_interval(entry),
        )
        self.delivered_receiver: list[dict] = []
        self.delivered_sender: list[dict] = []
        self.letters: list[dict] = []
        # API clients are reused across polls (each PostNLJouwAPI owns a
        # requests.Session with a connection pool); they are only rebuilt
        # when the access token actually changes.
        self.graphq_api: PostNLGraphql | None = None
        self.jouw_api: PostNLJouwAPI | None = None
        self._api_token: str | None = None
        # barcode -> last seen ParcelStatus. ``None`` on the first refresh so
        # we can suppress events for parcels that already existed when the
        # integration started (we do not know their previous state).
        self._known_state: dict[str, ParcelStatus] | None = None
        # barcode -> last seen (planned_from, planned_to) tuple. Mirrors
        # ``_known_state`` for delivery-time-change detection.
        self._known_delivery_times: (
            dict[str, tuple[str | None, str | None]] | None
        ) = None
        # barcode -> last seen ParcelStatus for *outgoing* (sender) parcels —
        # own sent shipments and returns both land in senderShipments. Tracked
        # over the full sender list so a status change or a
        # transition-to-delivered fires an outgoing event. ``None`` on the
        # first refresh for the same suppression reason as ``_known_state``.
        self._known_outgoing_state: dict[str, ParcelStatus] | None = None
        # Letter ids seen on the previous successful letters fetch. ``None``
        # on the first refresh for the same reason — we do not want to fire
        # ``postnl_letter_announced`` for every letter that already existed.
        self._known_letter_ids: set[str] | None = None
        # barcode -> last successfully transformed (normalized) parcel, so a
        # transient Track & Trace failure for one parcel degrades to its
        # previous data instead of failing the whole refresh.
        self._parcel_cache: dict[str, dict] = {}
        # barcode -> history timeline for *delivered* parcels. A delivered
        # parcel's history never changes, so the extra T&T call is made at
        # most once per barcode instead of on every poll.
        self._delivered_history_cache: dict[str, list[dict] | None] = {}
        # Cached device id for this account, attached to every fired event so
        # device-trigger automations can filter to a specific PostNL account.
        # ``None`` until the device exists (i.e. the sensors are set up).
        self._cached_device_id: str | None = None
        # Timestamp of the last successful poll, surfaced by a diagnostic
        # sensor so users can alert on a silently-stale integration (the
        # count sensors only change when a value changes, not every poll).
        self.last_success_time: datetime | None = None
        _LOGGER.debug("PostNLCoordinator initialized with update interval: %s", self.update_interval)

    def _device_id(self) -> str | None:
        """Resolve (and cache) this account's device id for event payloads.

        Looked up from the device registry by config entry. Stays ``None``
        until the device has been registered (the sensors create it on first
        setup), which is harmless because events are suppressed on the very
        first refresh anyway.
        """
        if self._cached_device_id is not None:
            return self._cached_device_id
        registry = dr.async_get(self.hass)
        device = next(
            iter(dr.async_entries_for_config_entry(registry, self.config_entry.entry_id)),
            None,
        )
        if device is not None:
            self._cached_device_id = device.id
        return self._cached_device_id

    async def _async_update_data(self) -> dict[str, list[dict]]:
        _LOGGER.debug("Starting data update for PostNL.")
        try:
            auth: AsyncConfigEntryAuth = self.config_entry.runtime_data.auth
            _LOGGER.debug("Authenticating with PostNL API.")
            await auth.check_and_refresh_token()

            if self.graphq_api is None or auth.access_token != self._api_token:
                self._api_token = auth.access_token
                self.graphq_api = PostNLGraphql(self._api_token)
                self.jouw_api = PostNLJouwAPI(self._api_token)

            data: dict[str, list[dict]] = {
                'receiver': [],
                'sender': []
            }

            shipments = await self.hass.async_add_executor_job(self.graphq_api.shipments)

            _LOGGER.debug("Shipments fetched: %s", shipments)
            receiver_shipments = [self.transform_shipment(shipment) for shipment in
                                  shipments.get('trackedShipments', {}).get('receiverShipments', [])]
            data['receiver'] = await asyncio.gather(*receiver_shipments)

            sender_shipments = [self.transform_shipment(shipment) for shipment in
                                shipments.get('trackedShipments', {}).get('senderShipments', [])]
            data['sender'] = await asyncio.gather(*sender_shipments)

            data['receiver'] = sort_parcels_by_ts(data['receiver'], 'planned_from')
            data['sender'] = sort_parcels_by_ts(data['sender'], 'planned_from')

            # Keep the fallback/history caches bounded to barcodes PostNL
            # still reports; anything older can never be needed again.
            current_barcodes = {
                p.get('barcode') for p in data['receiver'] if p.get('barcode')
            }
            self._parcel_cache = {
                k: v for k, v in self._parcel_cache.items() if k in current_barcodes
            }
            self._delivered_history_cache = {
                k: v
                for k, v in self._delivered_history_cache.items()
                if k in current_barcodes
            }

            # Incoming events over the full receiver list (active + delivered),
            # so the transition to delivered is visible in one set — mirrors
            # the outgoing events below.
            self._fire_change_events(data['receiver'])
            self._known_state = {
                p["barcode"]: p["status"]
                for p in data['receiver']
                if p.get("barcode")
            }
            self._known_delivery_times = {
                p["barcode"]: (p.get("planned_from"), p.get("planned_to"))
                for p in data['receiver']
                if p.get("barcode")
            }

            delivered_receiver = [p for p in data['receiver'] if p.get('delivered')]
            self.delivered_receiver = sort_parcels_by_ts(
                self._apply_delivered_filter(delivered_receiver),
                'delivered_at',
                descending=True,
            )

            # Outgoing events over the full sender list (active + delivered),
            # so a hop from in-transit to delivered is visible in one set.
            self._fire_outgoing_change_events(data['sender'])
            self._known_outgoing_state = {
                p["barcode"]: p["status"]
                for p in data['sender']
                if p.get("barcode")
            }

            delivered_sender = [p for p in data['sender'] if p.get('delivered')]
            self.delivered_sender = sort_parcels_by_ts(
                self._apply_delivered_filter(delivered_sender),
                'delivered_at',
                descending=True,
            )

            try:
                letters_payload = await self.hass.async_add_executor_job(self.jouw_api.letters)
                self.letters = extract_letters(letters_payload)
                self._fire_letter_events(self.letters)
                self._known_letter_ids = {
                    letter["id"] for letter in self.letters if letter.get("id")
                }
            except requests.exceptions.RequestException as exception:
                _LOGGER.warning("PostNL letters fetch failed: %s", exception)

            _LOGGER.info(
                "Updated PostNL data: %d receiver packages, %d sender packages, %d delivered shown, %d letters.",
                len(data['receiver']), len(data['sender']), len(self.delivered_receiver), len(self.letters),
            )

            self.last_success_time = datetime.now(timezone.utc)
            return data
        except ConfigEntryAuthFailed:
            raise
        except HomeAssistantError as exception:
            raise UpdateFailed("Authentication failed") from exception
        except requests.exceptions.RequestException as exception:
            raise UpdateFailed("Unable to update PostNL data") from exception

    def _fire_change_events(self, parcels: list[dict]) -> None:
        """Fire events for newly-registered parcels and parcel transitions.

        Silent on the very first refresh — we cannot reliably know which
        parcels are "new" to the user vs. "already there before HA started".
        From the second refresh onward, every not-yet-delivered parcel that
        was not present before yields one ``postnl_parcel_registered`` event,
        every parcel whose normalised status transitions **to** ``DELIVERED``
        yields one ``postnl_parcel_delivered`` event, every other status
        change yields one ``postnl_parcel_status_changed`` event, and every
        parcel whose ``planned_from`` or ``planned_to`` changed to a non-null
        value yields one ``postnl_parcel_delivery_time_changed`` event.
        ``delivered`` takes precedence over ``status_changed`` for the
        terminal hop, and a parcel first seen already-delivered fires
        nothing — mirroring the outgoing events.
        """
        if self._known_state is None:
            return

        known_times = self._known_delivery_times or {}
        device_id = self._device_id()

        for parcel in parcels:
            barcode = parcel.get("barcode")
            if not barcode:
                continue
            new_status = parcel["status"]
            if barcode not in self._known_state:
                if new_status != ParcelStatus.DELIVERED:
                    self.hass.bus.async_fire(
                        f"{DOMAIN}_parcel_registered",
                        {**parcel, "device_id": device_id},
                    )
                continue

            if self._known_state[barcode] != new_status:
                if new_status == ParcelStatus.DELIVERED:
                    self.hass.bus.async_fire(
                        f"{DOMAIN}_parcel_delivered",
                        {**parcel, "device_id": device_id},
                    )
                else:
                    self.hass.bus.async_fire(
                        f"{DOMAIN}_parcel_status_changed",
                        {
                            **parcel,
                            "device_id": device_id,
                            "old_status": self._known_state[barcode],
                            "new_status": new_status,
                        },
                    )

            old_from, old_to = known_times.get(barcode, (None, None))
            new_from = parcel.get("planned_from")
            new_to = parcel.get("planned_to")
            # Fire only when at least one of the two ends up with a real
            # (non-null) value AND that value differs from the last-known
            # one. value -> null transitions are intentionally silent —
            # they mean the carrier dropped the ETA, which is not what
            # users want to be paged about.
            from_changed = new_from is not None and new_from != old_from
            to_changed = new_to is not None and new_to != old_to
            if from_changed or to_changed:
                self.hass.bus.async_fire(
                    f"{DOMAIN}_parcel_delivery_time_changed",
                    {
                        **parcel,
                        "device_id": device_id,
                        "old_planned_from": old_from,
                        "new_planned_from": new_from,
                        "old_planned_to": old_to,
                        "new_planned_to": new_to,
                    },
                )

    def _fire_outgoing_change_events(self, parcels: list[dict]) -> None:
        """Fire status/delivered events for outgoing (sender) parcels.

        Silent on the very first refresh (``_known_outgoing_state is None``),
        matching ``_fire_change_events``. From the second refresh onward, every
        outgoing parcel whose normalised status transitions **to**
        ``DELIVERED`` yields one ``postnl_outgoing_parcel_delivered`` event, and
        every other status change yields one
        ``postnl_outgoing_parcel_status_changed`` event. ``delivered`` takes
        precedence over ``status_changed`` for that final transition, so the
        terminal hop fires exactly one (dedicated) event, not both. A parcel
        that is already delivered the first time it is seen never fires (its
        status did not change). There is no outgoing ``registered`` or
        ``delivery_time_changed`` event — those are intentionally out of scope.
        """
        if self._known_outgoing_state is None:
            return

        device_id = self._device_id()

        for parcel in parcels:
            barcode = parcel.get("barcode")
            if not barcode or barcode not in self._known_outgoing_state:
                continue
            old_status = self._known_outgoing_state[barcode]
            new_status = parcel["status"]
            if new_status == old_status:
                continue

            if new_status == ParcelStatus.DELIVERED:
                self.hass.bus.async_fire(
                    f"{DOMAIN}_outgoing_parcel_delivered",
                    {**parcel, "device_id": device_id},
                )
            else:
                self.hass.bus.async_fire(
                    f"{DOMAIN}_outgoing_parcel_status_changed",
                    {
                        **parcel,
                        "device_id": device_id,
                        "old_status": old_status,
                        "new_status": new_status,
                    },
                )

    def _fire_letter_events(self, letters: list[dict]) -> None:
        """Fire ``postnl_letter_announced`` for newly-seen letter ids.

        Silent on the very first refresh — we cannot tell which letters
        are "new" vs "already announced before HA started". From the
        second refresh onward, every letter whose id was not present in
        the previous successful fetch yields one event with the full
        letter payload plus ``carrier: "PostNL"``.
        """
        if self._known_letter_ids is None:
            return
        device_id = self._device_id()
        for letter in letters:
            letter_id = letter.get("id")
            if not letter_id or letter_id in self._known_letter_ids:
                continue
            self.hass.bus.async_fire(
                f"{DOMAIN}_letter_announced",
                {**letter, "carrier": "PostNL", "device_id": device_id},
            )

    def _apply_delivered_filter(self, parcels: list[dict]) -> list[dict]:
        """Trim the delivered receiver list according to the configured options."""
        options = self.config_entry.options
        filter_type = options.get(CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE)
        filter_amount = int(options.get(CONF_DELIVERED_FILTER_AMOUNT, DEFAULT_DELIVERED_FILTER_AMOUNT))

        if filter_type == "days":
            cutoff = datetime.now(timezone.utc) - timedelta(days=filter_amount)
            return [p for p in parcels if (dt := _delivery_dt(p)) is None or dt >= cutoff]

        return parcels[:filter_amount]

    @property
    def _include_history(self) -> bool:
        """Whether the opt-in per-parcel history option is enabled."""
        return bool(
            self.config_entry.options.get(
                CONF_INCLUDE_HISTORY, DEFAULT_INCLUDE_HISTORY
            )
        )

    def _delivered_history(self, shipment) -> list[dict] | None:
        """Fetch a history timeline for a delivered shipment (opt-in only).

        The active path gets observations from the Track & Trace call it
        already makes, but delivered parcels normally skip that call. When the
        history option is on we make the extra call here so delivered parcels
        get parity with DHL / DPD. A delivered parcel's history never changes,
        so a successful fetch is cached per barcode — one call per parcel
        ever, not one per poll. A failure is non-fatal — history is a
        nice-to-have, so we log and fall back to ``None`` (uncached, so the
        next poll retries) rather than failing the whole refresh.
        """
        if not self._include_history:
            return None
        barcode = shipment.get('barcode')
        if barcode and barcode in self._delivered_history_cache:
            return self._delivered_history_cache[barcode]
        try:
            details = self.jouw_api.track_and_trace(shipment['key'])
        except requests.exceptions.RequestException as exception:
            _LOGGER.warning(
                "History fetch failed for delivered shipment %s: %s",
                shipment.get('key'), exception,
            )
            return None
        colli = details.get('colli', {}).get(shipment['barcode'], {})
        history = build_history(_extract_observations(colli)) if colli else None
        if barcode:
            self._delivered_history_cache[barcode] = history
        return history

    async def transform_shipment(self, shipment) -> dict:
        _LOGGER.debug('Updating %s', shipment.get('key'))

        receiver_title = (shipment.get('receiverTitle') or '').strip() or None

        try:
            if shipment.get('delivered'):
                _LOGGER.debug('%s already delivered, no need to call jouw.postnl.', shipment.get('key'))

                # Delivered short-circuits the Track & Trace call, so there's no
                # ``colli.recipient.names.personName`` to read — the GraphQL
                # ``receiverTitle`` is the equivalent. No weight/dimensions
                # available on this path; the suite contract accepts those as
                # ``None``. History is the one exception: when the opt-in
                # option is on we make an extra T&T call for the timeline.
                history = await self.hass.async_add_executor_job(
                    self._delivered_history, shipment
                )
                return normalize_parcel({
                    "key": shipment.get('key'),
                    "barcode": shipment.get('barcode'),
                    "name": shipment.get('title'),
                    "url": shipment.get('detailsUrl'),
                    "shipment_type": shipment.get('shipmentType'),
                    "receiver_title": receiver_title,
                    "receiver": receiver_title,
                    "source_display_name": (
                        (shipment.get('sourceDisplayName') or '').strip()
                        or (shipment.get('title') or '').strip()
                        or None
                    ),
                    "status_message": "Pakket is bezorgd",
                    "delivered": shipment.get('delivered'),
                    "delivery_date": shipment.get('deliveredTimeStamp'),
                    "delivery_address_type": shipment.get('deliveryAddressType'),
                    "planned_date": None,
                    "planned_from": None,
                    "planned_to": None,
                    "expected_datetime": None,
                    "dimensions": None,
                }, history=history)

            _LOGGER.debug("Fetching Track and Trace details for shipment %s.", shipment['key'])
            try:
                track_and_trace_details = await self.hass.async_add_executor_job(
                    self.jouw_api.track_and_trace, shipment['key']
                )
            except requests.exceptions.RequestException as exception:
                # One broken T&T call must not fail the whole refresh. Degrade
                # per parcel: reuse the last successful transform when we have
                # one, otherwise fall through with the GraphQL-only fields.
                barcode = shipment.get('barcode')
                cached = self._parcel_cache.get(barcode) if barcode else None
                if cached is not None:
                    _LOGGER.warning(
                        "Track & Trace failed for shipment %s; reusing previous data: %s",
                        shipment.get('key'), exception,
                    )
                    return cached
                _LOGGER.warning(
                    "Track & Trace failed for shipment %s; using shipment fields only: %s",
                    shipment.get('key'), exception,
                )
                track_and_trace_details = {}

            if not track_and_trace_details.get('colli'):
                _LOGGER.debug("No colli found for shipment %s.", shipment['key'])
                _LOGGER.debug("Track and Trace response: %s", track_and_trace_details)

            colli = track_and_trace_details.get('colli', {}).get(shipment['barcode'], {})

            status_message = "Unknown"
            planned_date = planned_from = planned_to = expected_datetime = None
            recipient_name: str | None = None
            native_dimensions: dict | None = None
            history: list[dict] | None = None

            if colli:
                _LOGGER.debug("Colli details found for shipment %s: %s", shipment['key'], colli)
                if colli.get("routeInformation"):
                    route_information = colli.get("routeInformation")
                    planned_date = route_information.get("plannedDeliveryTime")
                    planned_from = route_information.get("plannedDeliveryTimeWindow", {}).get("startDateTime")
                    planned_to = route_information.get("plannedDeliveryTimeWindow", {}).get('endDateTime')
                    expected_datetime = route_information.get('expectedDeliveryTime')
                elif colli.get('eta'):
                    planned_date = colli.get('eta', {}).get('start')
                    planned_from = colli.get('eta', {}).get('start')
                    planned_to = colli.get('eta', {}).get('end')
                else:
                    planned_date = shipment.get('deliveryWindowFrom')
                    planned_from = shipment.get('deliveryWindowFrom')
                    planned_to = shipment.get('deliveryWindowTo')

                status_message = colli.get('statusPhase', {}).get('message', "Unknown")
                recipient_name = (
                    colli.get('recipient', {}).get('names', {}).get('personName')
                )
                native_dimensions = (colli.get('details') or {}).get('dimensions')
                # The active T&T call already carries the timeline; only build
                # it when the opt-in option is on.
                history = (
                    build_history(_extract_observations(colli))
                    if self._include_history
                    else None
                )
            else:
                _LOGGER.debug("Barcode not found in colli details for shipment %s.", shipment['key'])
                planned_date = shipment.get('deliveryWindowFrom')
                planned_from = shipment.get('deliveryWindowFrom')
                planned_to = shipment.get('deliveryWindowTo')

            parcel = normalize_parcel({
                "key": shipment.get('key'),
                "barcode": shipment.get('barcode'),
                "name": shipment.get('title'),
                "url": shipment.get('detailsUrl'),
                "shipment_type": shipment.get('shipmentType'),
                "receiver_title": receiver_title,
                # ``recipient.personName`` from Track & Trace is the
                # authoritative recipient name on the active path;
                # ``receiverTitle`` is the GraphQL fallback for when T&T
                # doesn't have it yet (first poll after registration).
                "receiver": recipient_name or receiver_title,
                "source_display_name": (
                        (shipment.get('sourceDisplayName') or '').strip()
                        or (shipment.get('title') or '').strip()
                        or None
                    ),
                "status_message": status_message,
                "delivered": shipment.get('delivered'),
                "delivery_date": shipment.get('deliveredTimeStamp'),
                "delivery_address_type": shipment.get('deliveryAddressType'),
                "planned_date": planned_date,
                "planned_from": planned_from,
                "planned_to": planned_to,
                "expected_datetime": expected_datetime,
                # Native PostNL dimensions (g + mm). Lives on the
                # intermediate dict so it surfaces under ``raw`` for power
                # users; ``normalize_parcel`` reads it and produces the
                # canonical kg + cm + ``text`` shape at the top level.
                "dimensions": native_dimensions,
            }, history=history)
            barcode = shipment.get('barcode')
            if barcode and colli:
                # Only a transform backed by real T&T data is worth reusing
                # as a fallback on a later transient failure.
                self._parcel_cache[barcode] = parcel
            return parcel
        except requests.exceptions.RequestException as exception:
            # Safety net for requests errors outside the targeted T&T catch.
            # Degrade to the last successful transform when we have one; only
            # fail the refresh when there is nothing to show for this parcel.
            _LOGGER.error("Error fetching Track and Trace details for shipment %s: %s", shipment.get('key'), exception, exc_info=True)
            barcode = shipment.get('barcode')
            cached = self._parcel_cache.get(barcode) if barcode else None
            if cached is not None:
                return cached
            raise UpdateFailed("Unable to update PostNL data") from exception
