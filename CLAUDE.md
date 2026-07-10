# Working in this repository

This is a Home Assistant custom integration for PostNL parcel tracking
plus MyMail letters and per-letter image entities. Distributed via HACS;
not part of HA core.

## Always consult HA developer documentation

Home Assistant's integration patterns evolve continuously. **Do not rely
on memory of past patterns** — fetch the canonical page before changing
a topic area, and check the developer blog before introducing anything
you only "know" from training data.

| When you change | Fetch first |
|---|---|
| Entity properties, naming, lifecycle, attributes | https://developers.home-assistant.io/docs/core/entity/ |
| Sensor specifics (state/device classes, units) | https://developers.home-assistant.io/docs/core/entity/sensor |
| Image entity (MyMail letter photos) | https://developers.home-assistant.io/docs/core/entity/image |
| Config flow, options flow, reauth, reconfigure | https://developers.home-assistant.io/docs/config_entries_config_flow_handler |
| DataUpdateCoordinator pattern | https://developers.home-assistant.io/docs/integration_fetching_data |
| Quality scale rules | https://developers.home-assistant.io/docs/core/integration-quality-scale |
| Diagnostics | https://developers.home-assistant.io/docs/core/integration/diagnostics |
| Translations | https://developers.home-assistant.io/docs/internationalization/core |

Branding is handled by the upstream `arjenbos/ha-postnl` brand assets
in the official `home-assistant/brands` repo (PostNL has a stable HA
core icon).

### Recent developer-facing changes

Before introducing patterns you only know from training data, check:

- https://developers.home-assistant.io/blog — API deprecations, new
  patterns, breaking changes. Recent posts trump older recollection.
- https://github.com/home-assistant/architecture/discussions — design
  decisions in flight that have not made it into stable docs yet.

## What is already in place

The integration is aligned with the **silver** quality scale tier. Don't
re-propose these as improvements:

- `quality_scale: "silver"` in manifest, minimum HA version `2024.7.0`
- `ConfigEntry.runtime_data` (typed dataclass `PostNLData` carrying
  `auth`, `coordinator`, `userinfo`)
- `PARALLEL_UPDATES = 0` in `sensor.py`
- Coordinator takes `config_entry=entry` so `self.config_entry` is
  available on the base class
- Per-parcel sensors are removed by the summary sensor
  (`PostNLIncomingParcelsSensor`) via `entity_registry.async_remove(entity_id)`
  when a barcode drops out of the coordinator data. The earlier
  self-remove pattern raced with coordinator-listener cleanup and left
  ghost entities behind — do not revert.
- Reauth flow uses `async_update_reload_and_abort` (one helper call
  instead of update + reload + abort)
- `aiohttp.ClientError` is intentionally not caught in the coordinator
  — `DataUpdateCoordinator` wraps it automatically. `requests` errors
  *are* caught because the executor-job calls re-raise them directly.
- Diagnostics handler in `diagnostics.py` with credential, token,
  e-mail, account-id, parcel and address fields all redacted
- Tests cover config flow, sensor, coordinator (incl. event firing),
  jouw_api, diagnostics, and setup/unload lifecycle
- `_unrecorded_attributes` on every summary sensor — parcel, shipment
  and letter lists are kept out of the recorder long-term tables
- `_attr_attribution = "Data provided by PostNL"` per entity
- Letters sensor and per-letter `ImageEntity` for MyMail
- **No `entry.add_update_listener`** — the OptionsFlow calls
  `self.hass.config_entries.async_schedule_reload(entry.entry_id)` on
  submit so a changed refresh interval takes effect immediately.
  Reauth still reloads via `async_update_reload_and_abort` (that is
  correct and unrelated). Combining an update listener with a
  reload-on-update flow is logged as a deprecation today and becomes
  an error in HA 2026.12+ — see the
  [config_entry_listener deprecation](https://developers.home-assistant.io/blog/2026/05/07/config-entry-listener-together-with-reloading-methods/).

### Adopted in 4.0.0 (do not refactor away)

- **`ParcelStatus` enum** in `const.py` — canonical carrier-agnostic
  statuses. `normalize_parcel` maps the Dutch `statusPhase.message`
  via `map_parcel_status` (ordered substring patterns) and reports
  `ParcelStatus.UNKNOWN` (with one-shot info log) for anything not
  yet in the map. The original PostNL string lives on the parcel's
  `raw_status` field; do not re-introduce it as the top-level
  `status` value.
- **Carrier-agnostic parcel shape**: every parcel exposed by the
  coordinator carries `carrier`, `barcode`, `sender`, `status`,
  `raw_status`, `delivered`, `delivered_at`, `planned_from`,
  `planned_to`, `pickup`, `pickup_point`, `url`, `raw`. (Extended in
  4.1.0 with `receiver`, `weight`, `dimensions` — see below.) Sensors
  read these keys; the original transformed PostNL payload lives
  under `raw`.
- **Events**: the coordinator fires `postnl_parcel_registered`,
  `postnl_parcel_status_changed`, `postnl_parcel_delivery_time_changed`
  (added in 4.1.0) and `postnl_letter_announced` on the HA event bus.
  All are suppressed on the very first refresh so we do not flood
  users with events for parcels or letters that already existed.
  `_known_letter_ids` mirrors `_known_state` and is reset only after a
  successful letters fetch. `delivery_time_changed` only fires when at
  least one of `planned_from` / `planned_to` ends up with a non-null
  value different from the previous one — value-to-null transitions
  are intentionally silent.
- **`has_entity_name = True`** on every entity, with `translation_key`
  routing names through `strings.json` and the language files. Drop
  `_attr_name` is the rule — translations are the source of truth.
- **Translated unit-of-measurement** (`entity.sensor.<key>.unit_of_measurement`
  in strings/translations). `_attr_native_unit_of_measurement` is
  intentionally absent.
- **`icons.json`** holds all sensor icons via the `translation_key`. Do
  not re-introduce `_attr_icon` on the sensor classes.
- **Device name pattern**: `"PostNL (<email>)"`. Sensors auto-prefix
  with this, yielding friendly names like
  `PostNL (account@example.com) Incoming parcels`.

### Adopted in 4.1.0 (do not refactor away)

- **Carrier-agnostic `receiver`, `weight`, `dimensions`** on every
  parcel. `receiver` comes from `colli.recipient.names.personName`
  (active path) or the GraphQL `receiverTitle` field (delivered
  short-circuit). `weight` and `dimensions` are sourced from
  `colli.details.dimensions` (native g + mm); `_convert_native_dimensions`
  turns them into the suite-wide canonical contract (kg + cm with the
  long edge as `length` rather than PostNL's `depth`, plus a
  pre-formatted `"L x W x H cm"` `text` string). The native dict
  stays on the intermediate parcel so it surfaces under `raw` for
  power users. Delivered parcels skip Track & Trace and therefore
  have no weight/dimensions (`None` for both).
- **Configurable refresh interval** via the options flow
  (`CONF_REFRESH_INTERVAL`; 15, 30, 60, 120 or 240 minutes; default
  30). The form is split into `delivered` and `polling` sections via
  `data_entry_flow.section`. The legacy hard-coded `POLL_INTERVAL`
  constant is gone — the coordinator reads `_refresh_interval(entry)`
  at startup, and the OptionsFlow triggers a reload on submit so a
  changed interval takes effect immediately. **Deliberate divergence**
  from the `ha-integration-knowledge` skill rule "polling intervals are
  NOT user-configurable": that rule targets HA Core integrations; this
  is a HACS integration where a user-tunable poll cadence is a wanted
  feature. Do not "fix" this to match the core rule.
- **Token-refresh resilience (do not weaken).** `check_and_refresh_token`
  mirrors the robustness of HA's `OAuth2Session` without adopting it
  (which would re-introduce the browser-extension onboarding the fork
  deliberately dropped):
  - **Preserves the refresh token** when PostNL's refresh response omits
    a new one (`new_token["refresh_token"] = old` fallback). Losing it
    used to force a full re-login on the next cycle.
  - **`asyncio.Lock` around refresh/re-login** with a re-check inside the
    lock, so two callers never spend the same rotating refresh token
    twice (PostNL rejects that).
  - **Transient re-login failures stay retryable.** Only a definitive
    credential rejection (`PostNLInvalidAuth`, raised when Capture returns
    no token) escalates to `ConfigEntryAuthFailed` / reauth. Any other
    `PostNLAuthError` (recaptcha, rate-limit, changed widget, mid-flow
    network blip) raises a generic `HomeAssistantError` → the coordinator
    turns it into a retryable `UpdateFailed`, and setup turns it into
    `ConfigEntryNotReady`. This is what stopped the "logged out roughly
    once a day" bug — do not collapse these back into one auth failure.

### Robustness (adopted after the 4.3.0 review — do not refactor away)

- **Reauth guards the account.** `reauth_confirm` calls
  `async_set_unique_id` + `_abort_if_unique_id_mismatch` so entering a
  *different* PostNL account's credentials aborts instead of rebinding
  the entry to another account.
- **Every `jouw_api` call has a `(10, 60)` timeout.** `requests` has no
  session-level default; without it a hanging PostNL server blocks an
  executor thread — and the whole refresh — indefinitely.
- **API clients are reused across polls.** `PostNLGraphql` /
  `PostNLJouwAPI` are only rebuilt when the access token changes
  (`_api_token` tracks it); each `PostNLJouwAPI` owns a
  `requests.Session` with a connection pool that would otherwise be
  recreated and leaked every poll.
- **One broken parcel no longer fails the refresh.** The active-path
  T&T call degrades per parcel: reuse the last successful transform for
  that barcode (`_parcel_cache`, only populated from transforms backed
  by real colli data, pruned to current barcodes each poll), else fall
  back to the GraphQL-only shipment fields. `UpdateFailed` is now the
  last resort when there is nothing at all to show for a parcel.

### Adopted in 4.2.0 — history (do not refactor away)

- **Per-parcel `history`** — a new top-level canonical field (alongside
  `status`, `weight`, …): an ordered list (oldest → newest) of
  `{timestamp, status, raw_status}` events, capped to the most recent
  `HISTORY_MAX_EVENTS` (20). Built by `build_history` in
  `coordinator.py` from the Track & Trace `analyticsInfo.allObservations`
  list (`_extract_observations` prefers it over the truncated
  `observations`). Kept identical across DHL / DPD / PostNL so the
  aggregator and cross-carrier dashboards read every carrier the same
  way. It is top-level (not under `raw`) so it survives the aggregator's
  `strip_raw()`.
- **Opt-in, default OFF.** Options-flow boolean `CONF_INCLUDE_HISTORY`
  in its own `history` section, `async_schedule_reload` on submit (same
  pattern as `CONF_REFRESH_INTERVAL`). When off, `history` is `None` —
  the key is never omitted (parity with `weight`/`dimensions`).
- **Delivered parcels get history too** (user decision, parity with
  DHL/DPD). The delivered short-circuit normally skips Track & Trace; when
  the option is on, `_delivered_history` makes the extra T&T call. That
  call is **non-fatal** — a `RequestException` there logs and falls back
  to `history = None` rather than failing the whole refresh. A successful
  fetch is cached per barcode (`_delivered_history_cache`) — a delivered
  parcel's timeline never changes, so it is one call per parcel ever, not
  one per poll; failures are NOT cached so the next poll retries.
- **Per-event status** maps from the stable `observationCode` via
  `_OBSERVATION_CODE_MAP` + `map_observation_status` (NOT the Dutch
  text). Unmapped codes → `null` (history) and a one-shot **warning**.
  The code catalogue lives in `docs/api/track_and_trace.md` (local-only).
- **Milestone vs meta + carry-forward (do not undo).** PostNL interleaves
  notification/admin/ETA events out of order. Only *milestone* codes
  (`_OBSERVATION_CODE_MAP`) carry a movement status; *meta* codes
  (`_OBSERVATION_META_CODES`, e.g. ETA recalcs, "bezorging wijzigen",
  data enrichment) carry **none** — `build_history` walks the events
  chronologically and makes a meta event **inherit the previous
  milestone's stage** (carry-forward) so the timeline never bounces
  backwards on a cosmetic event. Before the first milestone the carry
  baseline is `registered` (a tracked parcel is at least pre-announced),
  so a leading meta event like "Voorgemelde zending verrijkt" reads
  `registered` instead of a bare null. The one legitimate step-back is a real
  delivery delay/failure (`G01`/`G05`/`T04` → `in_transit`). Unmapped
  codes stay `null` (and do NOT carry forward — we genuinely don't know
  them). A fixed status for ETA codes is wrong by construction: the same
  ETA code reads `out_for_delivery` next to "Bezorger is onderweg" but
  `in_transit` right after sorting — carry-forward resolves both.
- **Feature B — unknown-status warnings.** Both the parcel status
  (`map_parcel_status`) and the history `observationCode`
  (`map_observation_status`) log **once per distinct unmapped value** at
  **WARNING** level with a copy-paste `issues/new` link (`_NEW_ISSUE_URL`).
  This replaced the old terse info log. Two parallel one-shot sets:
  `_LOGGED_UNKNOWN_STATUSES`, `_LOGGED_UNKNOWN_OBSERVATION_CODES`.
- **Recorder:** `history` is in `_unrecorded_attributes` on
  `PostNLParcelSensor`. Summary sensors already keep the whole parcel
  list out of the recorder via the `parcels` attribute.

### Adopted in 4.3.0 — device triggers + refresh button (do not refactor away)

- **`device_id` on every fired event.** Both `_fire_change_events` and
  `_fire_letter_events` resolve the account's device id once (cached in
  `self._cached_device_id`, looked up via
  `dr.async_entries_for_config_entry`) and add `device_id` to the three
  parcel events **and** `postnl_letter_announced`. Stays `None` until the
  device exists, which is fine — events are suppressed on the first
  refresh anyway. This is the key that lets device triggers filter
  per-account.
- **`device_trigger.py`** exposes four no-code device triggers:
  `parcel_registered` / `parcel_status_changed` /
  `parcel_delivery_time_changed` (identical to DHL/DPD) plus the
  PostNL-specific `letter_announced`. Delegates to
  `homeassistant.components.homeassistant.triggers.event` with
  `CONF_EVENT_DATA={device_id: ...}`. Trigger-type names live under
  `device_automation.trigger_type` in strings/translations.
- **Refresh `button`** (`Platform.BUTTON` first in `PLATFORMS`,
  `button.py`). One `PostNLRefreshButton` per account, unique_id
  `{account_id}_refresh`, `translation_key="refresh"`. `async_press`
  calls `async_request_refresh()` on the coordinator. Lands on the same
  `PostNL (<email>)` device.
- **Sensor cleanup is now sensor-scoped.** The setup-time stale-entity
  loop in `sensor.py` filters on `entity_entry.domain == "sensor"` before
  treating an `{account_id}_*` unique_id as a per-parcel barcode. Without
  this guard it deletes the refresh button (`{account_id}_refresh`) **and
  the letter image entities** (`{account_id}_letter_image_*`) on every
  setup. Do not drop the domain check.
- **Diagnostic `last_update` sensor** (`PostNLLastUpdateSensor`,
  unique_id `{account_id}_last_update`, `EntityCategory.DIAGNOSTIC`,
  device class TIMESTAMP). Reads `coordinator.last_success_time`, stamped
  with `datetime.now(timezone.utc)` just before `_async_update_data`
  returns. Lets users alert on a silently stale integration. **Must be in
  `non_parcel_unique_ids`** in `sensor.py` — it is a sensor whose
  unique_id starts with `{account_id}_`, so without the exclusion the
  setup cleanup loop deletes it as a stale parcel.
- **Deliveries `calendar`** (`Platform.CALENDAR` in `PLATFORMS`,
  `calendar.py`). One `PostNLDeliveriesCalendar` per account, unique_id
  `{account_id}_deliveries`, `translation_key="deliveries"`. Read-only
  view over the non-delivered `coordinator.data["receiver"]` parcels —
  **no extra API calls**, so enabled by default (no options toggle). One
  `CalendarEvent` per active incoming parcel with a `planned_from`; `end`
  is `planned_to` or `planned_from + 1h`. `event` returns the soonest
  event whose `end > dt_util.now()`. Summary = sender (falls back to
  barcode); pickup parcels set `location`. Letters are NOT on the calendar
  (no delivery moment). A combined cross-carrier calendar lives in the
  **aggregator**, not here.
- **README stays lean** (see suite README house style): no `## Buttons`
  or `## Device triggers` sections; the device-trigger option is a single
  sentence folded into **Events**. The button and calendar are not
  documented in the README at all (discoverable in the HA UI). CLAUDE.md
  still documents everything.

### Adopted after 4.4.0 — outgoing events (do not refactor away)

- **Two outgoing events fire from `PostNLCoordinator`**:
  `postnl_outgoing_parcel_status_changed` and
  `postnl_outgoing_parcel_delivered`, via `_fire_outgoing_change_events`,
  over the **full `data['sender']`** list (active + delivered, unfiltered by
  the delivered-display option) so a hop from in-transit to delivered is
  visible. Both own sent shipments and returns land in `senderShipments`, so
  this covers returns for free (see memory `returns_outgoing_parity`). State
  tracked in `_known_outgoing_state` (barcode → ParcelStatus), `None` on the
  first refresh for the same suppression reason as `_known_state`.
- **`delivered` takes precedence over `status_changed`** for the terminal
  transition (change **to** `ParcelStatus.DELIVERED` fires only `_delivered`).
  **No outgoing `registered` and no outgoing `delivery_time_changed`** — out
  of scope. Both carry `device_id`; wired into `device_trigger.py` as
  `outgoing_parcel_status_changed` / `outgoing_parcel_delivered` (now six
  device triggers incl. `letter_announced`) with labels under
  `device_automation.trigger_type`. Kept identical to DHL/DPD suite-wide.

## Planned for the next major bump

- **Exception translations** (Gold-tier rule). `UpdateFailed(...)`
  still uses f-strings; the Gold push will move to `translation_key`
  + `translation_placeholders`.
- **Per-letter events** (e.g. `postnl_letter_received`). Currently a
  user-side workaround exists by watching `sensor.postnl_letters`
  going up; a coordinator-side event would be nicer.

## Deliberately skipped (no plan to change)

- **Slimming `extra_state_attributes`** on summary sensors. The full
  parcel list stays; `_unrecorded_attributes` handles the recorder
  side.
- **`async-dependency` / `inject-websession`** (Platinum). The PostNL
  APIs use `requests` via executor jobs — switching to aiohttp would
  be a substantial refactor for marginal gain; listed for completeness.

## Repo-specific quirks

- **Three APIs**: GraphQL (`graphql.py` — shipment list), Track &
  Trace (`jouw_api.py` — per-shipment status, MyMail letters, MyMail
  image bytes), and Login (`login_api.py` — userinfo). All three
  share one bearer token managed by `auth.py`.
- **PKCE login flow with re-login fallback**: `AsyncConfigEntryAuth`
  first tries a refresh-token exchange. If that fails it re-runs the
  full username/password login. Reauth flow is the last resort. Order
  matters; don't reorder.
- **MyMail endpoint requires app-identification headers**: not just
  the bearer token. `PostNLJouwAPI.mymail_headers` carries
  `api-version`, `app-platform`, `device-token`, etc. These mirror
  the headers the PostNL mobile app sends. Values occasionally need
  bumping if PostNL ships a new app version.
- **MyMail uses a "server-driven UI" payload**: not a clean JSON list
  of letters. `extract_letters` walks `screen.sections[].items[]`
  looking for `type == "Letter"`. Dates come as `"16 juni"` (Dutch
  day-month, no year); `parse_letter_date` infers the year from
  PostNL's ~2-week retention window.
- **Letter image URLs require auth**: a Lovelace `<img>` cannot load
  them directly. The `PostNLLetterImage` entity fetches the bytes via
  `PostNLJouwAPI.image()` server-side and serves them through HA's
  authenticated image proxy. Do not change to a redirect-based scheme.
- **First refresh runs in `__init__.py`, before `async_forward_entry_setups`**:
  `async_setup_entry` sets `entry.runtime_data` (the coordinator reads
  `runtime_data.auth` in `_async_update_data`, so it must exist first)
  and then awaits `coordinator.async_config_entry_first_refresh()` before
  forwarding. Raising `ConfigEntryNotReady` from a *forwarded* platform is
  too late for HA to catch — it logs a warning and half-sets-up the entry.
  This also means a single first refresh instead of one per platform
  (`sensor.py` **and** `image.py` each used to call it). The image platform
  still relies on `coordinator.letters` being populated before it adds its
  initial batch of entities (a coordinator-listener callback after setup
  registers image entities that never reach the state machine); that data
  is now guaranteed present by the `__init__` first refresh. Do not move
  the first refresh back into a platform.
- **PostNL status is a Dutch human string, not an enum**:
  `colli.statusPhase.message` is whatever PostNL's UI happens to
  show — it changes without notice. `map_parcel_status` therefore
  uses ordered substring patterns rather than a dict lookup. More
  specific patterns must come before broader ones (e.g. "wordt
  vandaag bezorgd" before "bezorgd").
- **`jouw.postnl.nl` is the universal backend — never route to
  `jouw.postnl.be`.** Verified 2026-07-07 with a live NL token: the
  GraphQL `trackedShipments` inbox is **account-scoped, not
  domain-scoped** — `jouw.postnl.be/account/api/graphql` returns a
  byte-identical parcel list to `.nl` (same account, same backend, no
  `country`/`market` claim in the JWT). The `.be` host is strictly
  worse: MyMail `.../MyMail/letter` returns **HTTP 400** there (letter
  scanning is a Netherlands-only mail feature) while `.nl` returns 200.
  So a NL-vs-BE country dropdown would be a **no-op** for parcels and
  would break letters — do not add one. Belgian PostNL accounts are
  already fully covered by the existing `.nl` calls (documented in the
  README's Requirements). This is why "add postnl.be support" needs no
  code; the suite's real Belgium gap is **bpost**, a genuinely separate
  carrier.

## Fork / upstream relationship

This repository is a fork of
[`arjenbos/ha-postnl`](https://github.com/arjenbos/ha-postnl)
maintained by [@peternijssen](https://github.com/peternijssen).
HACS-visible releases ship from this fork; bug fixes that apply
upstream too are filed as separate PRs against `arjenbos/main`.
`manifest.json` still lists `@arjenbos` as codeowner because the
integration originated there. Cross-repo coordination is documented
in `CHANGES.md`.

## Running tests

```
python -m pytest tests/ --cov=custom_components.postnl
```

Coverage must stay **above 95%** (the silver `test-coverage` rule on
developers.home-assistant.io). Run before committing.
