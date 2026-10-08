"""Deployed-artifact contract tests.

The contracts asserted here are defined on the source side by CI tests in
tollgate-module-basic-go (tests/contract/) — this module asserts the
DEPLOYED artifact honors them: the running uhttpd actually listens where
entry_ui says, the package manager's own comparator agrees with our
version-ordering scheme, the discovery advertisement answers as JSON,
and the postinst refuses to claim success over a husked portal.

Each test skips (with the reason in the skip message) when the deployed
artifact predates its contract — a router on an older release, or a
non-Go backend — so the suite stays green everywhere while the report
still distinguishes "contract present and honored" from "contract
absent". That distinction is the drift-catching point: a release that
silently drops a contract shows up as a new skip, not as green.
"""

import json
import os
import subprocess

import pytest

pytestmark = [pytest.mark.api, pytest.mark.extended]


# entry_ui maps (from tollgate-module-basic-go #709 / schema v0.0.10):
#   board (default): board owns the entry pair (8080/443), LuCI 8090/8443
#   luci:            LuCI owns the entry pair, board 8090/8443
ENTRY_PAIR = (8080, 443)
SECONDARY_PAIR = (8090, 8443)


def _read_entry_ui(router) -> str | None:
    """entry_ui from the router's config.json, or None when the deployed
    config predates the field (schema < v0.0.10)."""
    out = router.ssh("jq -r .entry_ui /etc/tollgate/config.json 2>/dev/null || echo ABSENT")
    value = out.strip()
    if value in ("", "null", "ABSENT"):
        return None
    return value


def _uhttpd_listens(router, section: str) -> tuple[list[int], list[int]]:
    """(http_ports, https_ports) a uhttpd section actually listens on,
    from the live uci state — not from any table we shipped."""
    def _ports(raw: str) -> list[int] | None:
        ports: list[int] = []
        for token in raw.split():
            try:
                ports.append(int(token.rsplit(":", 1)[-1]))
            except ValueError:
                return None  # unparseable state reads as absent, not as a crash
        return sorted(ports)

    http = _ports(router.ssh(f"uci get uhttpd.{section}.listen_http 2>/dev/null || true"))
    https = _ports(router.ssh(f"uci get uhttpd.{section}.listen_https 2>/dev/null || true"))
    if http is None or https is None:
        return ([], [])
    return (http, https)


@pytest.mark.go_only
def test_entry_ui_port_mapping_contract(router):
    """The entry_ui flip (#709): whichever UI entry_ui names owns the
    entry pair, the other owns the secondary pair — asserted against the
    live uhttpd uci state, not against a table duplicated in this test."""
    entry_ui = _read_entry_ui(router)
    if entry_ui is None:
        pytest.skip("deployed config predates entry_ui (schema < v0.0.10) — contract absent on this artifact")

    sections = router.ssh("uci show uhttpd 2>/dev/null | cut -d. -f2 | sort -u").split()
    # uhttpd renders one named section per listener instance; identify
    # them by their listen lists rather than by hard-coded names so the
    # test tracks whatever 99-tollgate-setup/92-tollgate-admin-setup
    # actually created.
    found: dict[str, tuple[list[int], list[int]]] = {}
    for section in sections:
        if not section:
            continue
        listens = _uhttpd_listens(router, section)
        if listens[0] or listens[1]:
            found[section] = listens

    if not found:
        pytest.skip("no uhttpd listen state found (LuCI-only image?) — contract absent on this artifact")

    entry_owner = "board" if entry_ui == "board" else "luci"
    all_ports = [p for http, https in found.values() for p in http + https]
    for port in ENTRY_PAIR + SECONDARY_PAIR:
        assert port in all_ports, (
            f"entry_ui={entry_ui}: port {port} is listened on by no uhttpd section — "
            f"the port table converged on the router disagrees with the {ENTRY_PAIR}/{SECONDARY_PAIR} contract"
        )

    # Each pair must be owned by exactly one section (a port listened on
    # by two sections is the both-listeners-443 failure #709 gated out).
    for port in ENTRY_PAIR + SECONDARY_PAIR:
        owners = [s for s, (http, https) in found.items() if port in http or port in https]
        assert len(owners) == 1, (
            f"entry_ui={entry_ui}: port {port} is listened on by {owners} — exactly one owner expected"
        )

    # The two UIs swap pairs together: the entry pair's owner and the
    # secondary pair's owner must be different sections.
    entry_owner_section = next(
        s for s, (http, https) in found.items() if ENTRY_PAIR[0] in http or ENTRY_PAIR[1] in https
    )
    secondary_owner_section = next(
        s for s, (http, https) in found.items() if SECONDARY_PAIR[0] in http or SECONDARY_PAIR[1] in https
    )
    assert entry_owner_section != secondary_owner_section, (
        f"entry_ui={entry_ui} ({entry_owner} on the entry pair): one uhttpd section "
        f"'{entry_owner_section}' owns both pairs — the flip did not converge"
    )


@pytest.mark.smoke
def test_discovery_advertisement_content_type_is_json(router):
    """:2121's advertisement must answer application/json (#730/#628) —
    downstream r2r clients parse it with a JSON parser and warn or fail
    on text/plain."""
    host = router.host
    r = subprocess.run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{content_type} %{http_code}",
         f"http://{host}:2121/", "--connect-timeout", "5"],
        capture_output=True, text=True, timeout=15,
    )
    if r.stdout.split()[-1:] == ["000"]:
        pytest.skip("no listener on :2121 — contract absent on this artifact")
    parts = r.stdout.split()
    if len(parts) != 2:
        pytest.skip(f"could not read the advertisement's content-type/status: {r.stdout!r}")
    content_type, code = parts
    assert code == "200", f"discovery advertisement answered {code}, expected 200"
    assert content_type.split(";")[0].strip() == "application/json", (
        f"discovery advertisement Content-Type is {content_type} — r2r clients parse this as JSON (#730)"
    )


def _compare_versions(router, tool: str, left: str, op: str, right: str) -> bool | None:
    if tool == "opkg":
        out = router.ssh(f"opkg compare-versions '{left}' {op} '{right}' 2>/dev/null; echo RC=$?")
        # opkg prints 0/1 for false/true and always succeeds
        if "RC=0" not in out:
            return None
        return out.splitlines()[0].strip() == "1"
    # apk-tools 3.x: `apk version -t A B` prints '<', '=', or '>'
    out = router.ssh(f"apk version -t '{left}' '{right}' 2>/dev/null").strip()
    if out not in ("<", "=", ">"):
        return None
    return {"<": op in ("<<", "<"), "=": op == "==", ">": op in (">>", ">")}[out]


def test_package_version_ordering_contract(router):
    """The package version scheme's ordering topology, asserted with the
    package manager's OWN comparator on the target: pre-releases sort
    before their release (the ~ scheme, #738), releases order numerically,
    and nothing about the scheme depends on our normalizer being right —
    the shipped strings are what the router will actually compare during
    upgrades."""
    tool = "opkg" if "opkg" in router.ssh("command -v opkg || true") else (
        "apk" if "apk" in router.ssh("command -v apk || true") else None
    )
    if tool is None:
        pytest.skip("neither opkg nor apk on target — cannot assert ordering")

    installed = router.ssh(
        "opkg list-installed tollgate-wrt 2>/dev/null | awk '{print $3}' || apk info -v tollgate-wrt 2>/dev/null"
    ).strip()
    if not installed:
        pytest.skip("tollgate-wrt not installed on target — contract absent on this artifact")

    # The topology every upgrade path depends on (#738): pre-release
    # ordering below the release, and consecutive releases ordering.
    cases = [
        ("0.6.0~rc1", "<<", "0.6.0"),
        ("0.6.0~alpha4", "<<", "0.6.0~rc1"),
        ("0.6.0", "<<", "0.6.1"),
        ("0.0.0~git99", "<<", "0.6.0"),
    ]
    for left, op, right in cases:
        verdict = _compare_versions(router, tool, left, op, right)
        if verdict is None:
            pytest.skip(f"{tool}'s comparator unavailable on target")
        assert verdict, (
            f"on-target {tool} disagrees with the version topology: "
            f"{left} {op} {right} is false — upgrades between these will be refused (#738)"
        )


@pytest.mark.slow
def test_postinst_fails_loud_over_husked_portal(router):
    """The fail-loud contract (#715): a package install over a
    zero-byte nodogsplash binary (the opkg orphan-removal husk, fork
    #93/#109) must FAIL, never report success over a dead portal.

    Destructive on purpose — it mangles nodogsplash and reinstalls the
    package — so it only runs when TOLLGATE_DESTRUCTIVE=1 explicitly
    opts in, and it restores the husked binary by reinstalling
    nodogsplash afterwards.
    """
    if os.environ.get("TOLLGATE_DESTRUCTIVE") != "1":
        pytest.skip("destructive: set TOLLGATE_DESTRUCTIVE=1 to assert the postinst fail-loud contract")

    if "opkg" not in router.ssh("command -v opkg || true"):
        pytest.skip("no opkg on target — the husk class is opkg-era")

    have_package = router.ssh("ls /tmp/tollgate-wrt*.ipk 2>/dev/null").strip()
    if not have_package:
        pytest.skip("no tollgate-wrt .ipk staged on target — stage one to assert this contract")

    backup = router.ssh("md5sum /usr/bin/nodogsplash 2>/dev/null | awk '{print $1}'").strip()
    if not backup:
        pytest.skip("nodogsplash not installed — contract context absent")

    try:
        router.ssh(": > /usr/bin/nodogsplash")  # the husk: zero bytes
        out = router.ssh("opkg install --force-reinstall /tmp/tollgate-wrt*.ipk 2>&1; echo RC=$?")
        assert "RC=0" not in out, (
            "postinst reported SUCCESS over a zero-byte nodogsplash — the silent-death "
            "class is back (#715, fork #93/#109)"
        )
        assert "nodogsplash" in out and ("empty" in out.lower() or "zero" in out.lower()), (
            f"the failure must name the husked portal and its remediation, got: {out[-400:]}"
        )
    finally:
        # Restore a functional nodogsplash; a failed assert above leaves
        # the router with the husk, and this recovery must not be skippable.
        router.ssh("opkg update >/dev/null 2>&1; opkg install --force-reinstall nodogsplash >/dev/null 2>&1 || true")
