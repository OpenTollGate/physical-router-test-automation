"""Portal configuration — controls which captive portal is deployed to the router.

The built-in portal is shipped inside tollgate-wrt.ipk at
/etc/tollgate/tollgate-captive-portal-site and symlinked into
/etc/nodogsplash/htdocs during install.  It is the default and requires
no extra deployment step.

Alternative portals ship as separate .ipk packages that PROVIDE
tollgate-captive-portal-site and CONFLICT with the built-in one.
Installing them replaces the portal files in /etc/nodogsplash/htdocs
automatically.
"""

import os
import logging

log = logging.getLogger("tollgate.portal")

# Known portal packages.
_PORTAL_REGISTRY: dict[str, dict[str, str]] = {
    "builtin": {},
    "net4sats": {
        "repo": "Amperstrand/net4sats-captive-portal",
        "workflow": "build-package.yml",
        "package_name": "net4sats-captive-portal",
    },
}


class PortalConfig:

    def __init__(self, portal_type: str | None = None):
        self.type = (portal_type
                     or os.environ.get("TOLLGATE_PORTAL", "builtin")).lower()
        if self.type not in _PORTAL_REGISTRY:
            raise ValueError(
                f"Unknown portal: {self.type!r}. "
                f"Must be one of: {', '.join(_PORTAL_REGISTRY)}"
            )

    @property
    def is_builtin(self) -> bool:
        return self.type == "builtin"

    @property
    def repo(self) -> str | None:
        return _PORTAL_REGISTRY[self.type].get("repo")

    @property
    def workflow(self) -> str | None:
        return _PORTAL_REGISTRY[self.type].get("workflow")

    @property
    def package_name(self) -> str | None:
        return _PORTAL_REGISTRY[self.type].get("package_name")

    @property
    def needs_separate_deploy(self) -> bool:
        return self.type != "builtin"
