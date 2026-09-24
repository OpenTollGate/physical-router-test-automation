"""Install-path end-to-end helpers: artifact selection, identity gate, policy gate.

This module is the *decision* half of the dual-install-path coverage
(`tests/scenarios/test_install_paths.py`).  Everything that can be decided
without touching the bench router lives here as a pure function so it can be
unit-tested on any host:

* which published artifact matches a router (arch + package manager + format),
* whether the router's package manager can even install that artifact
  (OpenWrt 25.x is apk-only: an ``.ipk`` fails with
  ``ERROR: v2 package format error`` and exit 99),
* the sha256 manifest check for the release asset,
* the sha256 of ``usr/bin/tollgate-wrt`` *inside* the installed artifact
  (the artifact-identity gate),
* the expected captive-portal POLICY state (nodogsplash ``users_to_router``
  allow-set, the #566 admin-board nft guard, the ``:8090`` drop),
* the exact command that drives the existing happy-path Playwright suite,

plus thin SSH/HTTP command builders used by the scenario tests.

Nothing here mutates a router; the scenario tests do that, under the bench
lock (``lib.bench_lock``).
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants — feed release, package identity, policy surface
# ---------------------------------------------------------------------------

FEED_REPO = "FreedomTechFeed/packages"
FEED_RELEASE_DEFAULT = "v0.6.0-alpha4-pre16"
PACKAGE_NAME = "tollgate-wrt"

#: display version as it appears in asset filenames (tag minus leading "v",
#: dots in the pre-release part turned into underscores by the CI)
FEED_VERSION_DEFAULT = "0.6.0_alpha4_pre16"

#: arch of the bench GL-MT3000 (OpenWrt 25.12.5, mediatek/filogic)
BENCH_ARCH = "aarch64_cortex-a53"

ARTIFACT_FORMATS = ("apk", "ipk")
PACKAGE_MANAGERS = ("apk", "opkg")

#: nodogsplash users_to_router allow-list after 99-tollgate-setup.
#: 22 ssh, 23 telnet, 53 dns, 67 dhcp, 80 portal, 443, 2121 API,
#: 2050 portal stub, 2051 splash, 8080 LuCI admin.
POLICY_ALLOW_PORTS = frozenset({22, 23, 53, 67, 80, 443, 2050, 2051, 2121, 8080})

#: ports the policy must NOT expose to guests (the 8090/8443 admin-board pair)
POLICY_FORBIDDEN_PORTS = frozenset({8090, 8443})

#: the #566 guard written by 99-tollgate-setup
GUARD_NFT_FILE = "/etc/nftables.d/31-admin-board-not-guest-reachable.nft"
#: basename of the same file, for payload listings
GUARD_NFT_BASENAME = "31-admin-board-not-guest-reachable.nft"

#: the setup script must strip the admin-board ports from the NDS allow list
SETUP_SCRIPT = "/etc/uci-defaults/99-tollgate-setup"
SETUP_SKIP_PORTS = (8090, 8443)

#: port -> acceptable HTTP status codes when probed from a br-lan client
SURFACE_EXPECTATIONS: dict[int, frozenset[str]] = {
    2051: frozenset({"200"}),  # captive-portal SPA (uhttpd.portal)
    2050: frozenset({"200"}),  # nodogsplash portal stub
    2121: frozenset({"200"}),  # backend API
    8080: frozenset({"307"}),  # LuCI admin, redirects to the portal
}

#: :8090 must be unreachable from a br-lan client; curl reports "000" on refusal
UNREACHABLE_CODES = frozenset({"000", ""})

#: the artifact apk-format note for the .ipk rejection proof
IPK_REJECTION_NEEDLE = "v2 package format error"
IPK_REJECTION_EXIT = 99

#: the existing happy-path UI suite this module drives (do not invent a flow).
#: Path is relative to ``tests/`` — the cwd where ``playwright.config.mjs``
#: lives and therefore what the CLI resolves spec paths against.
HAPPY_PATH_SPEC = "protocol/captive-portal.spec.mjs"
HAPPY_PATH_GREP = "captive portal — happy path"
HAPPY_PATH_PROJECT = "desktop-portal"
HAPPY_PATH_EXPECTED_TITLES = (
    "API returns valid advertisement with pricing",
    "portal shows cashu token input",
    "portal shows lightning amount input",
    "portal shows mint selection pricing buttons",
)

#: canonical installer invocation (Felix's preferred test form: from a repo URL)
INSTALLER_SCRIPT_URL = (
    "https://raw.githubusercontent.com/OpenTollGate/tollgate-installer/main/install-and-test.sh"
)

OPKG_SKIP_REASON = (
    "router image uses opkg (OpenWrt <= 24.10): the direct-install scenario for an opkg image "
    "installs the .ipk. The pinned bench image is apk-only (OpenWrt 25.12.x), where an .ipk is "
    "NOT installable — apk rejects v2 packages with 'ERROR: v2 package format error' (exit 99). "
    "Recorded as an explicit skip with this reason; never treated as a pass."
)


class InstallPathError(RuntimeError):
    """Raised when the install path cannot proceed (bad artifact, wrong arch...)."""


class ArtifactNotFound(InstallPathError):
    """No published artifact matches the router's arch + package manager."""


# ---------------------------------------------------------------------------
# Release manifest / artifact selection
# ---------------------------------------------------------------------------


def release_asset_url(tag: str, filename: str, repo: str = FEED_REPO) -> str:
    """Return the GitHub download URL for a release asset."""
    return f"https://github.com/{repo}/releases/download/{tag}/{filename}"


def parse_manifest(text: str) -> dict[str, str]:
    """Parse a ``sha256sum``-style manifest into ``{filename: sha256}``.

    Tolerates the ``*`` binary marker and blank/comment lines.
    """
    entries: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^([0-9a-fA-F]{64})\s+\*?(.+)$", line)
        if not match:
            continue
        digest, filename = match.group(1).lower(), match.group(2).strip()
        entries[filename] = digest
    return entries


def artifact_filename(version: str, arch: str, fmt: str) -> str:
    """``tollgate-wrt_<version>_<arch>.<fmt>`` — the CI naming convention."""
    if fmt not in ARTIFACT_FORMATS:
        raise InstallPathError(f"unknown artifact format {fmt!r} (expected {ARTIFACT_FORMATS})")
    return f"{PACKAGE_NAME}_{version}_{arch}.{fmt}"


def package_manager_for_openwrt_version(version: str) -> str:
    """apk on OpenWrt >= 25.0, opkg on <= 24.10.  Accepts ``25.12.5``/``24.10.1``."""
    match = re.match(r"^\s*(\d+)", str(version))
    if not match:
        raise InstallPathError(f"cannot parse OpenWrt version {version!r}")
    return "apk" if int(match.group(1)) >= 25 else "opkg"


def format_for_package_manager(package_manager: str) -> str:
    """apk images take ``.apk``, opkg images take ``.ipk``."""
    if package_manager not in PACKAGE_MANAGERS:
        raise InstallPathError(
            f"unknown package manager {package_manager!r} (expected {PACKAGE_MANAGERS})"
        )
    return {"apk": "apk", "opkg": "ipk"}[package_manager]


@dataclass(frozen=True)
class Artifact:
    """A published release asset selected for a specific router."""

    filename: str
    sha256: str
    fmt: str
    arch: str
    release_tag: str
    url: str = ""

    def __post_init__(self) -> None:
        if not self.url:
            object.__setattr__(self, "url", release_asset_url(self.release_tag, self.filename))


@dataclass(frozen=True)
class ArtifactSelection:
    """Result of matching a release manifest against a router.

    ``artifact`` is set when a matching asset exists; ``skipped``/``skip_reason``
    carry the explicit, reported skip for the opkg (OpenWrt <= 24.10) branch.
    """

    artifact: Artifact | None
    skipped: bool = False
    skip_reason: str = ""

    def require(self) -> Artifact:
        if self.artifact is None:
            raise ArtifactNotFound(self.skip_reason or "no artifact selected")
        return self.artifact


def select_artifact(
    manifest: dict[str, str],
    arch: str,
    package_manager: str,
    *,
    release_tag: str = FEED_RELEASE_DEFAULT,
    version: str = FEED_VERSION_DEFAULT,
    repo: str = FEED_REPO,
) -> ArtifactSelection:
    """Select the release asset that this router can actually install.

    Selection is driven by the *detected package manager*, never by "the file
    that happens to be on disk": on an apk-only image an ``.ipk`` is rejected by
    apk with ``ERROR: v2 package format error`` (exit 99).
    """
    if package_manager == "opkg":
        return ArtifactSelection(artifact=None, skipped=True, skip_reason=OPKG_SKIP_REASON)

    fmt = format_for_package_manager(package_manager)
    filename = artifact_filename(version, arch, fmt)
    digest = manifest.get(filename)
    if digest is None:
        raise ArtifactNotFound(
            f"{filename} is not in the {release_tag} manifest "
            f"(manifest has {len(manifest)} entries; arch={arch}, format={fmt})"
        )
    return ArtifactSelection(
        artifact=Artifact(
            filename=filename,
            sha256=digest,
            fmt=fmt,
            arch=arch,
            release_tag=release_tag,
            url=release_asset_url(release_tag, filename, repo),
        )
    )


def verify_sha256(path: str | os.PathLike[str], expected: str) -> tuple[bool, str]:
    """Return ``(ok, actual_sha256)`` for *path* against *expected*."""
    actual = sha256_file(path)
    return actual == expected.lower(), actual


def sha256_file(path: str | os.PathLike[str]) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Artifact-identity gate: sha256 of usr/bin/tollgate-wrt inside the artifact
# ---------------------------------------------------------------------------

#: binary path inside both package formats
INSTALLED_BINARY = "/usr/bin/tollgate-wrt"


def binary_sha256_from_ipk(ipk_path: str | os.PathLike[str]) -> str:
    """sha256 of ``usr/bin/tollgate-wrt`` inside an ``.ipk`` (deb-style ar+tar).

    The CI builds the ``.ipk`` by hand (``packaging/build-ipk.sh``), so the
    payload binary is byte-identical to the compiled artifact.
    """
    with tempfile.TemporaryDirectory(prefix="ipk-extract-") as tmp:
        try:
            outer = tarfile.open(str(ipk_path), "r:gz")
        except (tarfile.TarError, OSError) as exc:
            raise InstallPathError(
                f"{os.path.basename(str(ipk_path))}: not a readable .ipk ({exc}) — "
                "an apk v3 artifact (magic 'ADBd') cannot be read as an ipk; "
                "use binary_sha256_from_artifact(path, 'apk') for that format"
            ) from exc
        with outer:
            if "./data.tar.gz" not in outer.getnames() and "data.tar.gz" not in outer.getnames():
                raise InstallPathError(
                    f"{ipk_path}: not an .ipk (no data.tar.gz member) — "
                    "did you pass an .apk to the ipk reader?"
                )
            member = outer.getmember("./data.tar.gz")
            outer.extract(member, tmp, filter="data")
        inner_path = Path(tmp) / "data.tar.gz"
        with tarfile.open(inner_path, "r:gz") as inner:
            if f".{INSTALLED_BINARY}" not in inner.getnames():
                raise InstallPathError(f"{ipk_path}: no {INSTALLED_BINARY} in payload")
            payload = inner.extractfile(f".{INSTALLED_BINARY}")
            if payload is None:  # pragma: no cover - defensive
                raise InstallPathError(f"{ipk_path}: {INSTALLED_BINARY} is not a regular file")
            return hashlib.sha256(payload.read()).hexdigest()


def apk_extract_command(apk_file: str, destination: str) -> list[str]:
    """Command that unpacks an apk (v2 *and* v3/ADB) package tree.

    apk v3 ``.apk`` files are ``ADB``-prefixed, not gzipped tars, so the
    extraction needs apk-tools.  ``apk.static`` from Alpine's
    ``apk-tools-static`` package is the portable way to get it on a test host.
    """
    tool = apk_tool()
    return [tool, "extract", "--allow-untrusted", "--destination", destination, str(apk_file)]


def apk_tool() -> str:
    """Locate an apk binary able to ``extract`` (env override, then PATH)."""
    explicit = os.environ.get("TOLLGATE_APK_TOOL", "")
    if explicit:
        if not os.path.isfile(explicit):
            raise InstallPathError(f"TOLLGATE_APK_TOOL={explicit} does not exist")
        return explicit
    found = shutil.which("apk") or shutil.which("apk.static")
    if not found:
        raise InstallPathError(
            "no apk tool on PATH: apk v3 artifacts (magic 'ADBd') cannot be unpacked with tar. "
            "Install Alpine's apk-tools-static and set TOLLGATE_APK_TOOL=<path to apk.static> "
            "(see docs/install-paths-e2e.md)."
        )
    return found


def binary_sha256_from_apk(apk_path: str | os.PathLike[str]) -> str:
    """sha256 of ``usr/bin/tollgate-wrt`` inside an ``.apk``.

    Requires apk-tools (see :func:`apk_tool`).  The hash is derived from the
    *same format* artifact that gets installed: the ``.apk`` and ``.ipk`` of one
    release are built by different CI jobs and their binaries are not
    byte-identical (see docs/install-paths-e2e.md).
    """
    with tempfile.TemporaryDirectory(prefix="apk-extract-") as tmp:
        result = subprocess.run(
            apk_extract_command(str(apk_path), tmp),
            capture_output=True,
            text=True,
            timeout=180,
        )
        if result.returncode != 0:
            raise InstallPathError(
                f"apk extract failed ({result.returncode}): "
                f"{(result.stderr or result.stdout).strip()[:300]}"
            )
        payload = Path(tmp) / INSTALLED_BINARY.lstrip("/")
        if not payload.is_file():
            raise InstallPathError(f"{apk_path}: no {INSTALLED_BINARY} in payload")
        return sha256_file(payload)


def binary_sha256_from_artifact(path: str | os.PathLike[str], fmt: str) -> str:
    """Expected installed-binary hash for the artifact of the given format."""
    if fmt == "ipk":
        return binary_sha256_from_ipk(path)
    if fmt == "apk":
        return binary_sha256_from_apk(path)
    raise InstallPathError(f"unknown artifact format {fmt!r}")


def ipk_payload_files(ipk_path: str | os.PathLike[str]) -> list[str]:
    """Every file path inside an ``.ipk`` payload (``usr/bin/...`` style)."""
    import io

    try:
        with tarfile.open(str(ipk_path), "r:gz") as outer:
            handle = outer.extractfile("./data.tar.gz")
            if handle is None:
                raise InstallPathError(f"{ipk_path}: no data.tar.gz member")
            data = handle.read()
    except (tarfile.TarError, OSError) as exc:
        raise InstallPathError(f"{ipk_path}: not a readable .ipk ({exc})") from exc
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as inner:
        return sorted(name.lstrip("./") for name in inner.getnames() if not name.endswith("/"))


def apk_payload_files(apk_path: str | os.PathLike[str]) -> list[str]:
    """Every file path inside an ``.apk`` payload (requires apk-tools)."""
    with tempfile.TemporaryDirectory(prefix="apk-list-") as tmp:
        result = subprocess.run(
            apk_extract_command(str(apk_path), tmp), capture_output=True, text=True, timeout=180
        )
        if result.returncode != 0:
            raise InstallPathError(
                f"apk extract failed ({result.returncode}): "
                f"{(result.stderr or result.stdout).strip()[:300]}"
            )
        root = Path(tmp)
        return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


def payload_files(path: str | os.PathLike[str], fmt: str) -> list[str]:
    if fmt == "ipk":
        return ipk_payload_files(path)
    if fmt == "apk":
        return apk_payload_files(path)
    raise InstallPathError(f"unknown artifact format {fmt!r}")


def payload_text(path: str | os.PathLike[str], fmt: str, member: str) -> str:
    """Read a text member (e.g. the setup script) out of the artifact payload."""
    member = member.lstrip("/")
    if fmt == "ipk":
        import io

        with tarfile.open(str(path), "r:gz") as outer:
            raw = outer.extractfile("./data.tar.gz")
            if raw is None:
                raise InstallPathError(f"{path}: no data.tar.gz member")
            data = raw.read()
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as inner:
            if f"./{member}" not in inner.getnames():
                raise InstallPathError(f"{path}: no {member} in payload")
            handle = inner.extractfile(f"./{member}")
            if handle is None:  # pragma: no cover - defensive
                raise InstallPathError(f"{path}: {member} is not a regular file")
            return handle.read().decode("utf-8", "replace")
    if fmt == "apk":
        with tempfile.TemporaryDirectory(prefix="apk-member-") as tmp:
            result = subprocess.run(
                apk_extract_command(str(path), tmp), capture_output=True, text=True, timeout=180
            )
            if result.returncode != 0:
                raise InstallPathError(f"apk extract failed: {(result.stderr or '').strip()[:200]}")
            target = Path(tmp) / member
            if not target.is_file():
                raise InstallPathError(f"{path}: no {member} in payload")
            return target.read_text(encoding="utf-8", errors="replace")
    raise InstallPathError(f"unknown artifact format {fmt!r}")


def artifact_policy_readiness(files: list[str], setup_script: str) -> dict:
    """Does this artifact *ship* the #566 policy material?

    The policy assertions in the scenario tests are only reachable with an
    artifact that (a) carries the guard file and (b) strips 8090/8443 from the
    NDS pre-auth allow list in its setup script.  When either is missing the
    policy gate cannot pass, and saying so up front is the difference between a
    diagnosable failure and a mysterious one.
    """
    listing = " ".join(files or [])
    script = setup_script or ""
    ships_guard = GUARD_NFT_BASENAME in listing
    removes_ports = {
        port: f"del_list nodogsplash.@nodogsplash[0].users_to_router='allow tcp port {port}'" in script
        for port in SETUP_SKIP_PORTS
    }
    problems: list[str] = []
    if not ships_guard:
        problems.append(
            f"the artifact payload does not contain {GUARD_NFT_BASENAME} — the #566 admin-board "
            "guard cannot be present after install, so the POLICY assertions are unreachable with "
            "this release (the fix must actually ship in the package)"
        )
    for port, ok in removes_ports.items():
        if not ok:
            problems.append(
                f"the artifact's {SETUP_SCRIPT} does not remove 'allow tcp port {port}' from "
                "nodogsplash users_to_router — a fresh install would leave the admin board on the "
                "pre-auth allow list"
            )
    return {
        "ships_guard_nft": ships_guard,
        "setup_removes_admin_ports": removes_ports,
        "problems": problems,
    }


def identity_violations(installed_sha256: str, expected_sha256: str, artifact_name: str) -> list[str]:
    """Artifact-identity gate: the installed binary must be the artifact's binary."""
    installed = (installed_sha256 or "").strip().split()[0].lower() if installed_sha256 else ""
    expected = (expected_sha256 or "").strip().lower()
    if not installed:
        return ["could not read sha256 of /usr/bin/tollgate-wrt on the router"]
    if not expected:
        return [f"could not derive the expected binary hash from {artifact_name}"]
    if installed != expected:
        return [
            "artifact-identity FAILED: installed /usr/bin/tollgate-wrt sha256 "
            f"{installed} != {expected} (from {artifact_name}) — the installed build is NOT the "
            "artifact under test"
        ]
    return []


#: arch tail of a package filename, e.g. "_aarch64_cortex-a53" / "_x86_64" / "_mips_24kc"
_ARCH_TAIL_RE = re.compile(
    r"_(?:aarch64|arm|mips64|mipsel|mips|x86_64|x86|i386|riscv64|powerpc)[a-z0-9_.\-]*$"
)


def version_stem_from_filename(artifact_name: str) -> str:
    """Version part of ``tollgate-wrt_<version>_<arch>.<fmt>`` -> ``<version>``."""
    basename = os.path.basename(artifact_name or "")
    if "_" not in basename:
        return ""
    rest = basename.split("_", 1)[1]
    rest = rest.rsplit(".", 1)[0] if "." in rest else rest
    return _ARCH_TAIL_RE.sub("", rest)


def version_violations(installed_version: str, artifact_name: str) -> list[str]:
    """The installed package version string must name the artifact's version."""
    version = (installed_version or "").strip()
    if not version:
        return ["installed package version string is empty (apk info -v / opkg list-installed)"]
    stem = version_stem_from_filename(artifact_name)
    if stem and stem not in version:
        return [
            f"installed version {version!r} does not contain the artifact version stem {stem!r} "
            f"(from {artifact_name})"
        ]
    return []


# ---------------------------------------------------------------------------
# POLICY gate
# ---------------------------------------------------------------------------

_USERS_TO_ROUTER_RE = re.compile(
    r"users_to_router\s*=\s*['\"]?allow\s+(?:tcp|udp)\s+port\s+(\d+)", re.IGNORECASE
)


def parse_users_to_router_ports(uci_show_output: str) -> set[int]:
    """Extract the allowed ports from ``uci show nodogsplash`` output."""
    return {int(match.group(1)) for match in _USERS_TO_ROUTER_RE.finditer(uci_show_output or "")}


def parse_surface_probe(output: str) -> dict[int, str]:
    """Parse ``port:code`` lines produced by the surface probe shell snippet."""
    codes: dict[int, str] = {}
    for match in re.finditer(r":?(\d{2,5})\s*[:=]?\s*(\d{3}|000)", output or ""):
        codes[int(match.group(1))] = match.group(2)
    return codes


def surface_violations(codes: dict[int, str]) -> list[str]:
    """Check :2051/:2050/:2121/:8080 and the :8090 drop."""
    problems: list[str] = []
    for port, acceptable in sorted(SURFACE_EXPECTATIONS.items()):
        actual = codes.get(port)
        if actual is None:
            problems.append(f":{port} not probed (expected {'/'.join(sorted(acceptable))})")
        elif actual not in acceptable:
            problems.append(f":{port} returned {actual}, expected {'/'.join(sorted(acceptable))}")
    guest_admin = codes.get(8090, "000")
    if guest_admin not in UNREACHABLE_CODES:
        problems.append(
            f":8090 answered {guest_admin} from a br-lan client — the admin board is "
            "guest-reachable, the #566 guard is gone"
        )
    return problems


def policy_violations(
    allowed_ports: set[int] | frozenset[int],
    *,
    guard_present: bool,
    guard_drops_br_lan: bool,
    ssh_alive: bool,
) -> list[str]:
    """Assert nodogsplash allow-set equality, the guard file, and SSH survival."""
    problems: list[str] = []
    missing = sorted(POLICY_ALLOW_PORTS - allowed_ports)
    extra = sorted(allowed_ports & POLICY_FORBIDDEN_PORTS)
    if missing:
        problems.append(f"nodogsplash users_to_router is missing {missing}")
    if extra:
        problems.append(f"nodogsplash users_to_router still allows the forbidden ports {extra}")
    unexpected = sorted(allowed_ports - POLICY_ALLOW_PORTS)
    if unexpected:
        problems.append(f"nodogsplash users_to_router has unexpected ports {unexpected}")
    if not guard_present:
        problems.append(f"guard file {GUARD_NFT_FILE} is missing")
    elif not guard_drops_br_lan:
        problems.append(f"guard file {GUARD_NFT_FILE} does not drop traffic from br-lan")
    if not ssh_alive:
        problems.append("SSH (port 22) is not alive after the install — the allow rule is gone")
    return problems


GUARD_DROP_MARKER = "br-lan"


def uci_show_users_to_router_command() -> str:
    return "uci show nodogsplash 2>/dev/null | grep users_to_router"


def users_to_router_from_nds_output(nds_output: str) -> set[int]:
    """nodogsplash also prints the allowed list on ``ndsctl`` output; best effort."""
    return {int(p) for p in re.findall(r"allow (?:tcp|udp) port (\d+)", nds_output or "")}


# ---------------------------------------------------------------------------
# Happy-path UI suite (reuse, do not reinvent)
# ---------------------------------------------------------------------------


PLAYWRIGHT_RELATIVE_CLI = "node_modules/.bin/playwright"


def playwright_cli(repo_root: str | os.PathLike[str] | None = None) -> tuple[list[str], str]:
    """Resolve the *repo's* Playwright test runner (not a global CLI).

    ``npx playwright test`` only works when ``@playwright/test`` is installed in
    the repo; a bare global ``playwright`` install has no ``test`` command and
    fails with ``unknown command 'test'``.  Returns ``(argv_prefix, source)``.
    """
    root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[1]
    local = root / PLAYWRIGHT_RELATIVE_CLI
    if local.is_file():
        return [str(local)], f"repo-local ({PLAYWRIGHT_RELATIVE_CLI})"
    raise InstallPathError(
        "the Playwright test runner is not installed in this repo "
        f"({local} missing): run `npm install` in {root} first — the reused happy-path "
        "suite needs @playwright/test, and a global `playwright` CLI has no `test` command"
    )


def happy_path_suite_command(
    project: str = HAPPY_PATH_PROJECT, *, repo_root: str | os.PathLike[str] | None = None
) -> list[str]:
    """The exact command that drives the *existing* happy-path Playwright suite.

    Suite: ``tests/protocol/captive-portal.spec.mjs`` (``describe('captive portal
    — happy path')``), the same spec ``make test-captive-portal-happy`` runs.
    Run it with cwd=``tests/`` (where ``playwright.config.mjs`` lives).
    """
    prefix, _source = playwright_cli(repo_root)
    return [
        *prefix,
        "test",
        "--config=playwright.config.mjs",
        f"--project={project}",
        "--grep",
        HAPPY_PATH_GREP,
        HAPPY_PATH_SPEC,
    ]


@dataclass
class HappyPathResult:
    """Outcome of driving the reused happy-path suite."""

    passed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    total: int = 0
    exit_code: int | None = None

    def violations(self, *, allow_skip: bool = False) -> list[str]:
        problems: list[str] = []
        if self.total == 0:
            problems.append(
                "the happy-path UI suite ran 0 tests (collection error or a bad --grep) — "
                "an empty run is not a pass"
            )
        if self.failed:
            problems.append(f"happy-path UI suite failures: {self.failed}")
        if self.missing:
            problems.append(f"expected happy-path tests missing from the run: {self.missing}")
        if self.skipped and not allow_skip:
            problems.append(
                f"happy-path UI tests were skipped ({self.skipped}) — a skipped happy path is not "
                "a completed happy path; set TOLLGATE_HAPPY_PATH_ALLOW_SKIP=1 to record this as a "
                "warning instead"
            )
        return problems


def parse_happy_path_report(report: dict) -> HappyPathResult:
    """Parse a Playwright JSON report into a :class:`HappyPathResult`."""

    def _walk(suites: list[dict]) -> list[dict]:
        collected: list[dict] = []
        for suite in suites or []:
            collected.extend(_walk(suite.get("suites") or []))
            collected.extend(suite.get("specs") or [])
        return collected

    result = HappyPathResult()
    for spec in _walk(report.get("suites") or []):
        title = spec.get("title") or ""
        result.total += 1
        tests = spec.get("tests") or []
        statuses = {(t.get("status") or "").lower() for t in tests}
        # project/status annotations: skipped wins over passed only if everything skipped
        if statuses and statuses <= {"skipped"}:
            result.skipped.append(title)
        elif "failed" in statuses or "timedout" in statuses or "interrupted" in statuses:
            result.failed.append(title)
        elif "passed" in statuses:
            result.passed.append(title)
        else:
            result.skipped.append(title)

    for expected in HAPPY_PATH_EXPECTED_TITLES:
        if not any(expected == title for title in result.passed + result.failed + result.skipped):
            result.missing.append(expected)
    return result


# ---------------------------------------------------------------------------
# Command builders for the scenario tests
# ---------------------------------------------------------------------------

SURFACE_PROBE_SNIPPET = (
    "for p in 2051 2050 2121 8080 8090; do "
    "printf ':%s ' $p; "
    'curl -s -o /dev/null -w "%{http_code}\\n" -m 6 "http://127.0.0.1:$p/" 2>/dev/null || echo 000; '
    "done"
)


def apk_install_command(remote_path: str) -> str:
    """Direct install of a *staged* artifact, unsigned, with the CA-less flag."""
    return f"apk add --no-check-certificate --allow-untrusted {remote_path}"


def opkg_install_command(remote_path: str) -> str:
    return f"opkg install --force-overwrite {remote_path}"


def install_command(package_manager: str, remote_path: str) -> str:
    if package_manager == "apk":
        return apk_install_command(remote_path)
    if package_manager == "opkg":
        return opkg_install_command(remote_path)
    raise InstallPathError(f"unknown package manager {package_manager!r}")


def package_version_command(package_manager: str) -> str:
    if package_manager == "apk":
        return f"apk info -v 2>/dev/null | grep -i '^{PACKAGE_NAME}'"
    return f"opkg list-installed {PACKAGE_NAME} 2>/dev/null"


def installed_binary_hash_command() -> str:
    return f"sha256sum {INSTALLED_BINARY} 2>/dev/null"


def openwrt_release_command() -> str:
    return ". /etc/openwrt_release 2>/dev/null; echo \"$DISTRIB_DESCRIPTION|$DISTRIB_ARCH|$DISTRIB_TARGET\""


def installed_arch_command(package_manager: str) -> str:
    if package_manager == "apk":
        return "apk info -v 2>/dev/null | head -1; apk --print-arch 2>/dev/null || true"
    return "opkg print-architecture 2>/dev/null | head -3"


def installer_command(
    router_ip: str,
    password: str,
    ln_address: str,
    *,
    tag: str = FEED_RELEASE_DEFAULT,
    script_url: str = INSTALLER_SCRIPT_URL,
) -> list[str]:
    """The canonical installer path, exactly as an operator runs it.

    ``bash <(curl -fsSL <raw url>) --tag <tag> <router> <password> <ln-address>``
    — run through ``bash -c`` because ``<(…)`` is a bash process-substitution.
    """
    shell = (
        f"bash <(curl -fsSL {script_url}) --tag {tag} "
        f"{router_ip} {shlex_quote(password)} {shlex_quote(ln_address)}"
    )
    return ["bash", "-c", shell]


def shlex_quote(value: str) -> str:
    import shlex

    return shlex.quote(value)


def installer_supports_tag(script_text: str) -> bool:
    """True when the fetched installer advertises ``--tag`` (see dry-run report)."""
    return bool(re.search(r"^\s*--tag\)", script_text or "", re.MULTILINE)) or "--tag <tag>" in (
        script_text or ""
    )


# ---------------------------------------------------------------------------
# Host-side fetch + probe helpers
# ---------------------------------------------------------------------------


def http_get(url: str, *, timeout: int = 60, insecure: bool = False) -> bytes:
    """GET a URL, returning the body.  ``insecure`` skips TLS verification."""
    import ssl

    context = None
    if insecure or os.environ.get("TOLLGATE_INSECURE_TLS") == "1":
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    request = urllib.request.Request(url, headers={"User-Agent": "prta-install-paths/1.0"})
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
        return response.read()


def download(url: str, destination: str | os.PathLike[str], *, timeout: int = 300) -> str:
    """Download *url* to *destination* (creating parent dirs) and return the path."""
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "prta-install-paths/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response, open(target, "wb") as handle:
        shutil.copyfileobj(response, handle)
    return str(target)


def fetch_release_manifest(
    tag: str = FEED_RELEASE_DEFAULT, *, repo: str = FEED_REPO, client: object | None = None
) -> dict[str, str]:
    """Download and parse ``SHA256SUMS`` for a feed release."""
    del client  # kept for signature symmetry with the github client helper
    url = release_asset_url(tag, "SHA256SUMS", repo)
    return parse_manifest(http_get(url).decode("utf-8", "replace"))


def tcp_probe(host: str, port: int, *, timeout: float = 5.0) -> bool:
    """TCP connect probe.  This router DROPS ICMP — never use ping."""
    import socket

    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def is_ipv4(value: str) -> bool:
    try:
        ipaddress.IPv4Address(str(value).strip())
        return True
    except ValueError:
        return False


def parse_report_json(path: str | os.PathLike[str]) -> dict:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


# ---------------------------------------------------------------------------
# Bench lock integration (thin re-export so scenarios import one module)
# ---------------------------------------------------------------------------


def bench_lock(purpose: str, *, task_id: str | None = None, path: str | None = None):
    """Convenience factory for the bench lock; see :mod:`lib.bench_lock`."""
    from lib.bench_lock import BenchLock

    return BenchLock(purpose=purpose, task_id=task_id, path=path)
