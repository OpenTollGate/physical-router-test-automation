# Router Inventory

Copy `routers.example.json` to `routers.json` when you need multiple physical routers. Do not commit `routers.json`; it may contain lab-specific hostnames, SSIDs, or other operational details.

Each router can define `luciUrl`, `sshHost`, `sshUser`, `arch`, `wifiInterface`, `tollgateSsidPrefix`, and optional non-secret metadata such as `model`.
