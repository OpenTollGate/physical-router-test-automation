#!/usr/bin/env python3
"""cost-status.py — list everything you are currently being billed for.

Covers SHC (services, snapshots, backups) and GCP (instances, disks,
snapshots, images, addresses, machine-images). Each resource is classified
against the allowlist in config/approved-resources.yaml:

  APPROVED               matches an allowlist entry (reason shown)
  ⚠ STOPPED-BUT-BILLABLE VM exists but is powered off — still bills
  ✗ UNAPPROVED           billable and not on the allowlist (exit 1)

Also prints actual spend from the SHC ledger over 24h/7d/30d plus the
current burn rate (projection if nothing changes). GCP costs are estimates
(the lab service account cannot read the Billing API); SHC costs are exact.

Usage:
    scripts/cost-status.py                    # human report, both providers
    scripts/cost-status.py --provider shc     # SHC only
    scripts/cost-status.py --json             # machine-readable
    scripts/cost-status.py --export-reaper-env  # emit SHC_REAPER_EXTRA_KEEP_PATTERNS

Exit codes: 0 = all billable resources approved, 1 = unapproved resources
(or a billable resource exists that the allowlist does not cover), 2 = no
provider could be queried.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from typing import Any

DEFAULT_CONFIG = os.path.join(os.path.dirname(__file__), "..", "config", "approved-resources.yaml")

WINDOWS_H = {"24h": 24, "7d": 24 * 7, "30d": 24 * 30}

# ── Pricing estimates (USD) ─────────────────────────────────────────────
# GCP on-demand vCPU+RAM per hour for machine types seen in this lab.
GCP_HOURLY: dict[str, float] = {
    "e2-micro": 0.008392,
    "e2-small": 0.016769,
    "e2-medium": 0.033538,
    "e2-standard-2": 0.067075,
    "e2-standard-4": 0.134149,
    "e2-standard-8": 0.268299,
    "n1-standard-1": 0.047476,
    "n1-standard-2": 0.094951,
    "n2-standard-2": 0.097159,
    "n2-standard-4": 0.194318,
    "n2-standard-8": 0.388636,
}
GCP_GB_MONTH: dict[str, float] = {
    "disk:pd-standard": 0.04,
    "disk:pd-balanced": 0.10,
    "disk:pd-ssd": 0.17,
    "snapshot": 0.026,   # multi-regional storage
    "image": 0.10,       # custom image storage
    "machine-image": 0.10,
}
GCP_UNUSED_ADDRESS_DAY = 0.16  # reserved static IPv4, not attached


# ── Data model ──────────────────────────────────────────────────────────

@dataclass
class Billable:
    provider: str            # "shc" | "gcp"
    kind: str                # vm | snapshot | backup | instance | disk | image | address | machine-image
    name: str
    state: str               # running | stopped | active | reserved | in-use | ...
    daily_cost: float        # best-known USD/day (exact for SHC, estimate for GCP)
    cost_basis: str          # "exact" | "estimate:<detail>" | "unknown"
    region: str = ""
    created: str = ""        # ISO-ish, display only
    labels: dict[str, str] = field(default_factory=dict)
    extra: str = ""          # free-form annotation shown in output


@dataclass
class Rule:
    pattern: str | None
    labels: dict[str, str]
    reason: str

    def matches(self, r: Billable) -> bool:
        if self.pattern and re.search(self.pattern, r.name):
            return True
        if self.labels and all(r.labels.get(k) == v for k, v in self.labels.items()):
            return True
        return False


def load_rules(path: str) -> tuple[list[Rule], str]:
    """Returns (rules, error). error is empty on success."""
    try:
        import yaml
    except ImportError:
        return [], "PyYAML not installed"
    try:
        with open(path) as f:
            doc = yaml.safe_load(f) or {}
    except FileNotFoundError:
        return [], f"config not found: {path}"
    except Exception as e:  # malformed YAML
        return [], f"cannot parse {path}: {e}"
    rules = []
    for entry in doc.get("approved", []) or []:
        rules.append(Rule(
            pattern=entry.get("pattern"),
            labels={str(k): str(v) for k, v in (entry.get("labels") or {}).items()},
            reason=str(entry.get("reason", "")),
        ))
    return rules, ""


def parse_order_tag(ssh_key: str) -> str:
    """Extract the orderer from an SHC VM's stored public key comment.

    Prefers the toolkit's '#shc-order=' tag; falls back to the raw key
    comment (e.g. 'macbook@mbp.lan'); empty when keyless or unrecognized.
    """
    parts = (ssh_key or "").strip().split(None, 2)
    if len(parts) < 3 or not parts[0].startswith(("ssh-", "ecdsa-", "sk-")):
        return ""
    comment = parts[2]
    m = re.search(r"#shc-order=(\S+)", comment)
    if m:
        return m.group(1)
    return comment.strip()


# ── Classification (pure — unit-tested) ────────────────────────────────

APPROVED = "APPROVED"
UNAPPROVED = "UNAPPROVED"
STOPPED_WARN = "STOPPED-BUT-BILLABLE"   # VM-level warning, orthogonal to approval


def classify(resources: list[Billable], rules: list[Rule]) -> list[dict[str, Any]]:
    """Attach approval + warning flags. Returns report rows."""
    rows = []
    for r in resources:
        rule = next((x for x in rules if x.matches(r)), None)
        approved = rule is not None
        stopped_warning = r.kind in ("vm", "instance") and r.state not in ("running",)
        rows.append({
            "resource": r,
            "approved": approved,
            "reason": rule.reason if rule else "",
            "stopped_warning": stopped_warning,
        })
    return rows


def worst_problem(rows: list[dict[str, Any]]) -> str:
    """'' when fine, else the failure cause (drives exit code 1)."""
    unapproved = [x for x in rows if not x["approved"]]
    return f"{len(unapproved)} unapproved billable resource(s)" if unapproved else ""


# ── SHC ledger + spend reconstruction (pure — unit-tested) ─────────────

def parse_shc_time(s: str) -> dt.datetime | None:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            d = dt.datetime.strptime(s, fmt)
            return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
        except (ValueError, TypeError):
            continue
    return None


def parse_any_time(s: str) -> dt.datetime | None:
    """SHC 'YYYY-MM-DD HH:MM:SS' (UTC) or ISO-8601 (GCP creationTimestamp)."""
    t = parse_shc_time(s)
    if t is not None:
        return t
    try:
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
    except (ValueError, TypeError, AttributeError):
        return None


def window_credits(items: list[dict[str, Any]], now: dt.datetime) -> dict[str, float]:
    """USD credited (topups + cancel refunds) per window, from the SHC ledger.

    SHC charges no debits to this ledger — renewals draw down credit silently
    and invoices stay empty — so actual historical spend is not exposed by the
    API. Use reconstruct_spend() for a spend estimate.
    """
    out: dict[str, float] = {}
    for label, hours in WINDOWS_H.items():
        cutoff = now - dt.timedelta(hours=hours)
        total = 0.0
        for it in items:
            ts = parse_shc_time(str(it.get("date_added", "")))
            if ts is None or ts < cutoff:
                continue
            try:
                total += float(it.get("amount", 0) or 0)
            except (TypeError, ValueError):
                continue
        out[label] = total
    return out


def reconstruct_spend(resources: list[Billable], now: dt.datetime) -> dict[str, float]:
    """Estimated spend per window: Σ daily_cost × days the resource existed
    inside the window. Only covers resources that still exist (canceled
    services are gone from the APIs; their refunds appear as ledger credits).
    """
    out: dict[str, float] = {label: 0.0 for label in WINDOWS_H}
    for r in resources:
        if not r.created or r.daily_cost <= 0:
            continue
        created = parse_any_time(r.created)
        if created is None:
            continue
        age_h = max(0.0, (now - created).total_seconds() / 3600)
        for label, hours in WINDOWS_H.items():
            out[label] += r.daily_cost * min(hours, age_h) / 24
    return out


# ── SHC collector ───────────────────────────────────────────────────────

def _shc_client() -> Any:
    sys_path = os.environ.get("SHC_TOOLKIT_PATH", "")
    guesses = [sys_path] if sys_path else []
    guesses += [os.path.expanduser("~/src/shc-toolkit"), "/Users/macbook/src/shc-toolkit"]
    for g in guesses:
        if g and g not in sys.path and os.path.isdir(g):
            sys.path.insert(0, g)
            break
    from shc_toolkit.client import SHCClient
    return SHCClient()


def _shc_daily(pricing: dict[str, Any]) -> tuple[float, str]:
    """Normalize SHC pricing dict to USD/day."""
    try:
        price = float(pricing.get("price", 0) or 0)
        period = pricing.get("period", "day")
        term = int(pricing.get("term", 1) or 1)
    except (TypeError, ValueError):
        return 0.0, "unknown:unparsable-pricing"
    per_day = {"day": price / term, "week": price / (term * 7),
               "month": price / (term * 30), "year": price / (term * 365)}.get(period)
    if per_day is None:
        return 0.0, f"unknown:period-{period}"
    return per_day, "exact"


def collect_shc() -> tuple[list[Billable], dict[str, Any], str]:
    """Returns (resources, ledger{items,balance}, error)."""
    try:
        client = _shc_client()
        vms = client.list_vms()
        resources = []
        for v in vms:
            sid = int(v["id"])
            try:
                detail = client.get_vm_detail(sid)
            except Exception:
                detail = v
            runtime = (detail.get("runtime") or {})
            state = runtime.get("state") or v.get("service_status", "unknown")
            daily, basis = _shc_daily(detail.get("pricing") or {})
            ordered_by = parse_order_tag(detail.get("ssh_key") or "")
            resources.append(Billable(
                provider="shc", kind="vm",
                name=v.get("hostname", f"service-{sid}"),
                state=state, daily_cost=daily, cost_basis=basis,
                created=str(v.get("date_created", "")),
                extra=(f"ordered-by: {ordered_by}; " if ordered_by else "")
                      + f"service {sid}; renews {v.get('date_renews', '?')}",
            ))
            try:
                for snap in client.list_snapshots(sid) or []:
                    resources.append(Billable(
                        provider="shc", kind="snapshot",
                        name=snap.get("name") or snap.get("id", f"snap-{sid}"),
                        state="active",
                        daily_cost=float(snap.get("size_gb", 0) or 0) * 0.026 / 30,
                        cost_basis="estimate:snapshot-gb",
                        extra=f"service {sid}; {snap.get('size_gb', '?')}GB",
                    ))
            except Exception:
                pass
            try:
                for bak in client.list_backups(sid) or []:
                    resources.append(Billable(
                        provider="shc", kind="backup",
                        name=bak.get("name") or bak.get("id", f"backup-{sid}"),
                        state="active",
                        daily_cost=float(bak.get("size_gb", 0) or 0) * 0.026 / 30,
                        cost_basis="estimate:backup-gb",
                        extra=f"service {sid}; {bak.get('size_gb', '?')}GB",
                    ))
            except Exception:
                pass
        ledger_items: list[dict[str, Any]] = []
        try:
            limit, offset = 100, 0
            for _ in range(5):  # ≤500 items — far beyond 30d of renewals
                page = client.list_transactions(limit=limit, offset=offset)
                batch = page.get("items", []) if isinstance(page, dict) else page
                ledger_items.extend(batch)
                total = page.get("pagination", {}).get("total", len(ledger_items)) if isinstance(page, dict) else len(ledger_items)
                offset += limit
                if offset >= int(total or 0) or not batch:
                    break
        except Exception:
            pass
        balance = None
        try:
            balance = client.get_billing_balance()
        except Exception:
            pass
        return resources, {"items": ledger_items, "balance": balance}, ""
    except Exception as e:
        return [], {}, f"SHC unavailable: {e}"


# ── GCP collector ───────────────────────────────────────────────────────

def _gcloud(args: list[str]) -> Any:
    r = subprocess.run(["gcloud", *args, "--format=json"],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip().splitlines()[-1] if r.stderr else "gcloud failed")
    return json.loads(r.stdout or "[]")


def _last(url: str) -> str:
    return url.rstrip("/").split("/")[-1] if url else ""


def collect_gcp(project: str) -> tuple[list[Billable], str]:
    """Returns (resources, error). One failed listing aborts GCP (auth issue)."""
    try:
        resources: list[Billable] = []
        for inst in _gcloud(["compute", "instances", "list", "--project", project]):
            mt = _last(inst.get("machineType", ""))
            hourly = GCP_HOURLY.get(mt)
            running = inst.get("status", "") in ("RUNNING", "PROVISIONING", "STAGING")
            basis = f"estimate:{mt}" if hourly is not None else f"unknown:{mt}"
            daily = hourly * 24 if (hourly is not None and running) else 0.0
            zone = _last(inst.get("zone", ""))
            resources.append(Billable(
                provider="gcp", kind="instance", name=inst.get("name", "?"),
                state=(inst.get("status", "?") or "?").lower(),
                daily_cost=daily, cost_basis=basis, region=zone,
                created=str(inst.get("creationTimestamp", "")),
                labels={k: str(v) for k, v in (inst.get("labels") or {}).items()},
                extra="" if running else "compute not billed while stopped (disk billed separately)",
            ))
        for disk in _gcloud(["compute", "disks", "list", "--project", project]):
            dtype = _last(disk.get("type", ""))
            gb = float(disk.get("sizeGb", 0) or 0)
            rate = GCP_GB_MONTH.get(f"disk:{dtype}", 0.10)
            attached = bool(disk.get("users"))
            resources.append(Billable(
                provider="gcp", kind="disk", name=disk.get("name", "?"),
                state=("in-use" if attached else "orphaned"),
                daily_cost=gb * rate / 30, cost_basis=f"estimate:{dtype}",
                region=_last(disk.get("zone", "")),
                labels={k: str(v) for k, v in (disk.get("labels") or {}).items()},
                extra=f"{gb:g}GB {'attached' if attached else 'NOT attached'}",
            ))
        for snap in _gcloud(["compute", "snapshots", "list", "--project", project]):
            gb = float(snap.get("storageBytes", 0) or 0) / 1e9
            resources.append(Billable(
                provider="gcp", kind="snapshot", name=snap.get("name", "?"),
                state="active", daily_cost=gb * GCP_GB_MONTH["snapshot"] / 30,
                cost_basis="estimate:snapshot-gb",
                labels={k: str(v) for k, v in (snap.get("labels") or {}).items()},
                extra=f"{gb:.1f}GB",
            ))
        for img in _gcloud(["compute", "images", "list", "--no-standard-images", "--project", project]):
            gb = float(img.get("archiveSizeBytes", 0) or 0) / 1e9
            resources.append(Billable(
                provider="gcp", kind="image", name=img.get("name", "?"),
                state="active", daily_cost=gb * GCP_GB_MONTH["image"] / 30,
                cost_basis="estimate:image-gb",
                labels={k: str(v) for k, v in (img.get("labels") or {}).items()},
                extra=f"{gb:.2f}GB",
            ))
        for addr in _gcloud(["compute", "addresses", "list", "--project", project]):
            in_use = addr.get("status") == "IN_USE"
            resources.append(Billable(
                provider="gcp", kind="address", name=addr.get("name", "?"),
                state=("in-use" if in_use else "reserved"),
                daily_cost=0.0 if in_use else GCP_UNUSED_ADDRESS_DAY,
                cost_basis="estimate:unused-ipv4",
                region=_last(addr.get("region", "")),
                extra="" if in_use else "reserved static IP — billed while unattached",
            ))
        for mi in _gcloud(["compute", "machine-images", "list", "--project", project]):
            gb = float(mi.get("totalStorageBytes", 0) or 0) / 1e9
            resources.append(Billable(
                provider="gcp", kind="machine-image", name=mi.get("name", "?"),
                state="active", daily_cost=gb * GCP_GB_MONTH["machine-image"] / 30,
                cost_basis="estimate:machine-image-gb",
                extra=f"{gb:.1f}GB",
            ))
        return resources, ""
    except FileNotFoundError:
        return [], "GCP unavailable: gcloud CLI not installed"
    except Exception as e:
        return [], f"GCP unavailable: {e}"


# ── Output ──────────────────────────────────────────────────────────────

def _fmt_day(v: float) -> str:
    return f"${v:.2f}/day" if v else "$0"


def render_report(rows: list[dict[str, Any]], ledger: dict[str, Any],
                  burn_per_day: float, provider_errors: dict[str, str]) -> str:
    lines: list[str] = []
    lines.append("=" * 78)
    lines.append("PAID RESOURCES — everything currently billing")
    lines.append("=" * 78)
    if not rows:
        lines.append("  (none)")
    for row in sorted(rows, key=lambda x: (x["resource"].provider, x["resource"].kind, x["resource"].name)):
        r: Billable = row["resource"]
        flag = "✓ APPROVED" if row["approved"] else "✗ UNAPPROVED"
        warn = "  ⚠ STOPPED-BUT-BILLABLE" if row["stopped_warning"] else ""
        basis = "" if r.cost_basis == "exact" else f"  [{r.cost_basis}]"
        reason = f"  ({row['reason']})" if row["reason"] else ""
        lines.append(
            f"  {flag}{warn}  {r.provider}:{r.kind}  {r.name}  ({r.state})  "
            f"{_fmt_day(r.daily_cost)}{basis}{reason}"
        )
        if r.extra:
            lines.append(f"      {r.extra}")
    lines.append("")
    lines.append("-" * 78)
    lines.append("CONSUMPTION")
    lines.append("-" * 78)
    now = dt.datetime.now(dt.timezone.utc)
    spend = reconstruct_spend([row["resource"] for row in rows], now)
    items = ledger.get("items") or []
    lines.append("    window   est.spend*   credits added (topups+refunds)")
    credits_by_window = window_credits(items, now) if items else {}
    for label in ("24h", "7d", "30d"):
        credit_s = f"${credits_by_window[label]:.2f}" if label in credits_by_window else "n/a"
        lines.append(f"    {label:<8} ${spend[label]:<10.2f} {credit_s}")
    lines.append("    * reconstructed: Σ price/day × days-existed in window, current resources only")
    bal = ledger.get("balance")
    if bal:
        try:
            avail = next((b["available_credit"] for b in bal.get("balances", [])
                          if b.get("currency") == "USD"), None)
            if avail is not None:
                lines.append(f"  SHC balance: ${float(avail):.2f} available credit")
        except (TypeError, ValueError):
            pass
    lines.append(f"  Current burn rate: ${burn_per_day:.2f}/day "
                 f"→ 7d ${burn_per_day * 7:.2f}, 30d ${burn_per_day * 30:.2f} (if nothing changes)")
    lines.append("  GCP costs are estimates — lab SA cannot read the Billing API;")
    lines.append("  exact figures: Console → Billing → Reports")
    if provider_errors:
        lines.append("")
        for prov, err in provider_errors.items():
            lines.append(f"  NOTE {prov}: {err}")
    return "\n".join(lines)


def export_reaper_env(rules: list[Rule]) -> str:
    """Comma-joined patterns (anchors stripped) for the substring-matching reaper."""
    parts = []
    for rule in rules:
        if rule.pattern:
            parts.append(rule.pattern.lstrip("^").rstrip("$"))
    return "export SHC_REAPER_EXTRA_KEEP_PATTERNS='" + ",".join(parts) + "'"


# ── Main ────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default=DEFAULT_CONFIG, help="approved-resources.yaml path")
    ap.add_argument("--provider", default="shc,gcp", help="comma list: shc,gcp (default both)")
    ap.add_argument("--gcp-project", default=os.environ.get("TOLLGATE_GCP_PROJECT", "tollgate-test-lab"))
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--export-reaper-env", action="store_true",
                    help="print SHC_REAPER_EXTRA_KEEP_PATTERNS export line and exit")
    args = ap.parse_args(argv)

    rules, err = load_rules(args.config)
    if args.export_reaper_env:
        print(export_reaper_env(rules))
        return 0

    wanted = [p.strip() for p in args.provider.split(",") if p.strip()]
    resources: list[Billable] = []
    ledger: dict[str, Any] = {}
    errors: dict[str, str] = {}
    if err:
        errors["config"] = err + " — every billable resource will be UNAPPROVED"

    if "shc" in wanted:
        shc_res, shc_ledger, shc_err = collect_shc()
        resources.extend(shc_res)
        if shc_err:
            errors["shc"] = shc_err
        else:
            ledger = shc_ledger
    if "gcp" in wanted or "gcloud" in wanted:
        gcp_res, gcp_err = collect_gcp(args.gcp_project)
        resources.extend(gcp_res)
        if gcp_err:
            errors["gcp"] = gcp_err

    rows = classify(resources, rules)
    burn = sum(r.daily_cost for r in resources)

    if args.json:
        now = dt.datetime.now(dt.timezone.utc)
        items = ledger.get("items") or []
        print(json.dumps({
            "resources": [{**asdict(row["resource"]),
                           "approved": row["approved"],
                           "reason": row["reason"],
                           "stopped_warning": row["stopped_warning"]} for row in rows],
            "burn_per_day": round(burn, 4),
            "spend_reconstructed": {k: round(v, 4) for k, v in reconstruct_spend(resources, now).items()},
            "credits_added": window_credits(items, now) if items else None,
            "errors": errors,
        }, indent=2, default=str))
    else:
        print(render_report(rows, ledger, burn, errors))

    problem = worst_problem(rows)
    if problem:
        print(f"\n✗ {problem} — add to {args.config} or delete them", file=sys.stderr)
        return 1
    if errors and not resources and len(errors) >= len(wanted):
        print("✗ no provider could be queried", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
