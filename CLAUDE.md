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
| Diagnostics | https://developers.home-assistant.io/docs/integration_diagnostics |
| Translations | https://developers.home-assistant.io/docs/internationalization/core |
| Brand registration | https://developers.home-assistant.io/docs/creating_integration_brand |

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
- Per-parcel sensors self-remove via `async_remove(force_remove=True)`
- Coordinator logs warnings on unavailability (auth and connectivity)
- Reauth flow calls `async_reload` so new credentials propagate to the
  in-memory auth state
- Diagnostics handler in `diagnostics.py` with credential, token, e-mail,
  account-id, parcel and address fields all redacted
- Tests cover config flow, sensor, coordinator helpers,
  `transform_shipment`, jouw_api, diagnostics, and setup/unload
  (≥75% required for silver)
- `_unrecorded_attributes` on every summary sensor — parcel, shipment
  and letter lists are kept out of the recorder long-term tables
- `_attr_attribution = "Data provided by PostNL"` per entity (sensors
  and the letter image entity)
- Letters sensor and per-letter `ImageEntity` for MyMail

## What was deliberately skipped

- **`has_entity_name`** is *not* used on this integration. Switching to
  it would change friendly names for existing dashboards and automations.
  The user weighed this trade-off explicitly. Do not change it without
  asking.

## Repo-specific quirks

- **Three APIs**: GraphQL (`graphql.py` — shipment list), Track & Trace
  (`jouw_api.py` — per-shipment status, MyMail letters, MyMail image
  bytes), and Login (`login_api.py` — userinfo). All three share one
  bearer token managed by `auth.py`.
- **PKCE login flow with re-login fallback**: `AsyncConfigEntryAuth`
  first tries a refresh-token exchange. If that fails it re-runs the
  full username/password login. Reauth flow is the last resort. Order
  matters; don't reorder.
- **MyMail endpoint requires app-identification headers**: not just the
  bearer token. `PostNLJouwAPI.mymail_headers` carries `api-version`,
  `app-platform`, `device-token`, etc. These were lifted from a
  decompiled PostNL Android APK (`browser_extensions/` has the original
  cookie-scraper). Headers occasionally need bumping if PostNL ships a
  new app version.
- **MyMail uses a "server-driven UI" payload**: not a clean JSON list of
  letters. `extract_letters` walks `screen.sections[].items[]` looking
  for `type == "Letter"`. Dates come as `"16 juni"` (Dutch day-month,
  no year); `parse_letter_date` infers the year from PostNL's ~2-week
  retention window.
- **Letter image URLs require auth**: a Lovelace `<img>` cannot load
  them directly. The `PostNLLetterImage` entity fetches the bytes via
  `PostNLJouwAPI.image()` server-side and serves them through HA's
  authenticated image proxy. Do not change to a redirect-based scheme.
- **First-refresh ordering matters for image platform**: image entities
  added from a coordinator-listener callback after platform setup
  register in the entity registry but never make it into the state
  machine. `image.py` works around this by awaiting
  `async_config_entry_first_refresh()` and adding the initial batch
  during setup; the listener only handles later changes.

## Running tests

```
python -m pytest tests/ --cov=custom_components.postnl
```

Coverage must stay ≥75% (silver requirement). Run before committing.
