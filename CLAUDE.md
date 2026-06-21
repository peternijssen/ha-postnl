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
- Per-parcel sensors self-remove via `async_remove(force_remove=True)`
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
  `planned_to`, `pickup`, `pickup_point`, `url`, `raw`. Sensors read
  these keys; the original transformed PostNL payload lives under
  `raw`.
- **Events**: the coordinator fires `postnl_parcel_registered` and
  `postnl_parcel_status_changed` on the HA event bus. Events are
  suppressed on the very first refresh so we do not flood users with
  "registered" events for parcels that already existed.
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
  `api-version`, `app-platform`, `device-token`, etc. These were
  lifted from a decompiled PostNL Android APK. Headers occasionally
  need bumping if PostNL ships a new app version.
- **MyMail uses a "server-driven UI" payload**: not a clean JSON list
  of letters. `extract_letters` walks `screen.sections[].items[]`
  looking for `type == "Letter"`. Dates come as `"16 juni"` (Dutch
  day-month, no year); `parse_letter_date` infers the year from
  PostNL's ~2-week retention window.
- **Letter image URLs require auth**: a Lovelace `<img>` cannot load
  them directly. The `PostNLLetterImage` entity fetches the bytes via
  `PostNLJouwAPI.image()` server-side and serves them through HA's
  authenticated image proxy. Do not change to a redirect-based scheme.
- **First-refresh ordering matters for image platform**: image
  entities added from a coordinator-listener callback after platform
  setup register in the entity registry but never make it into the
  state machine. `image.py` works around this by awaiting
  `async_config_entry_first_refresh()` and adding the initial batch
  during setup; the listener only handles later changes.
- **PostNL status is a Dutch human string, not an enum**:
  `colli.statusPhase.message` is whatever PostNL's UI happens to
  show — it changes without notice. `map_parcel_status` therefore
  uses ordered substring patterns rather than a dict lookup. More
  specific patterns must come before broader ones (e.g. "wordt
  vandaag bezorgd" before "bezorgd").

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
