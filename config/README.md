# Router Inventory

Copy `routers.example.json` to `routers.json` when you need multiple physical routers. Do not commit `routers.json`; it may contain lab-specific hostnames, SSIDs, or other operational details.

Each router can define `luciUrl`, `sshHost`, `sshUser`, `arch`, `wifiInterface`, `tollgateSsidPrefix`, and optional non-secret metadata such as `model`.

## SSID prefix fields (optional)

The firmware derives SSIDs from one 4-char device code (`uci tollgate.device.code`): captive SSID `<brand>-<code>`, private SSID `<nym>-<code>` (nym default `c08r4d0r`). Tests resolve the actual SSIDs from the router at runtime; these fields are the fallback/classification hints:

- `captiveSsidPrefixes` — list (or comma-separated string) of captive brand prefixes, e.g. `["TollGate-", "Net4sats-"]` for branded routers. Defaults to `["TollGate-"]` (`TOLLGATE_CAPTIVE_SSID_PREFIXES` env, then legacy `TOLLGATE_SSID_PREFIX`).
- `privateSsidPrefix` — private SSID prefix (nym override). Defaults to `c08r4d0r` (`TOLLGATE_PRIVATE_SSID_PREFIX` env).
- `tollgateSsidPrefix` — legacy single captive prefix; still honored when `captiveSsidPrefixes` is absent.

All prefixes may be written with or without the trailing dash.

## Fleet manifests (multi-contributor capacity)

`fleets.example.yaml` is the schema example for `lib/fleet_capacity.py`. Each
fleet is an independently owned scope with its own coordinator, host
controller, allowed places, permitted actions and capacity limits; there is no
global default coordinator or host, so an undeclared fleet id raises instead of
inheriting one.

Copy it to a local path and pass that path explicitly to
`lib.fleet_capacity.load_manifest()` — no environment variable selects a
manifest, so a stray process cannot be pointed at someone else's fleet by
accident. Real manifests are never committed: they name contributor
infrastructure. Credentials are `(source, name)` *references* (for example an
OpenBao path), never values.

`configs/labgrid/inventory.local.yaml.example` is unchanged and remains the
single-owner physical bench adapter — it is not extended into this schema.
