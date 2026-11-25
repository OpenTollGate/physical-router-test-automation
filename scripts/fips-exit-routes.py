"""Pure, lane-specific route parser, NOT a RecoveryEvidence collector or executor.

Caller must supply the complete successful `ip -j -4 route show table 200`
stdout and its trusted query table. JSON alone cannot prove completeness,
namespace identity, freshness, or ownership. No Verified/Ownership attestations
are produced. Nothing here queries the kernel or applies recovery operations.
"""
import json


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON attribute: ' + key)
        result[key] = value
    return result


def parse_routes(raw, *, query_table):
    """Return Go RecoveryRoute-shaped dicts, or refuse the entire inventory.

    Only the two observed/expected iproute2 shapes below are supported; new
    attributes or output variants require explicit review, never filtering.
    """
    if type(query_table) is not int or query_table != 200:
        raise ValueError('trusted IPv4 table-200 query context required')
    routes = json.loads(raw, object_pairs_hook=_unique_object)
    if not isinstance(routes, list):
        raise ValueError('complete route inventory must be a JSON array')
    safety = dict(type='unreachable', dst='default', protocol='static',
                  metric=42760, flags=[])
    tunnel = dict(dst='default', dev='exit-test', protocol='static',
                  scope='link', metric=100, flags=[])
    result = []
    seen = set()
    for route in routes:
        if not isinstance(route, dict) or type(route.get('metric')) is not int:
            raise ValueError('route object with integer metric required')
        if route == safety:
            kind, device, metric = 'unreachable', '', 42760
        elif route == tunnel:
            kind, device, metric = 'unicast', 'exit-test', 100
        else:
            raise ValueError('foreign or uncertain route')
        if kind in seen:
            raise ValueError('duplicate route')
        seen.add(kind)
        result.append(dict(Type=kind, Destination='default', Device=device,
                           Protocol='static', Table=query_table, Metric=metric, Exact=True))
    if 'unreachable' not in seen:
        raise ValueError('unreachable safety default missing')
    return result
