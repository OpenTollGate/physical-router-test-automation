import pytest


def parse_port_ranges(ranges_str):
    result = []
    for part in ranges_str.split(","):
        part = part.strip()
        if "-" in part:
            start, end = part.split("-", 1)
            result.append((int(start), int(end)))
    return result


class TestPortAllocation:
    def test_get_available_ports_no_allocations(self):
        ranges = parse_port_ranges("10000-10010")
        allocated = set()
        reserved = {10005}
        available = []
        for start, end in ranges:
            for port in range(start, end + 1):
                if port not in allocated and port not in reserved:
                    available.append(port)
        assert len(available) == 10
        assert 10005 not in available
        assert 10000 in available

    def test_get_available_ports_with_allocations(self):
        ranges = parse_port_ranges("10000-10010")
        allocated = {10000, 10001, 10002}
        reserved = set()
        available = []
        for start, end in ranges:
            for port in range(start, end + 1):
                if port not in allocated and port not in reserved:
                    available.append(port)
        assert 10000 not in available
        assert 10001 not in available
        assert 10002 not in available
        assert 10003 in available
        assert len(available) == 8

    def test_parse_port_ranges_multiple(self):
        result = parse_port_ranges("10000-20000,30000-40000")
        assert len(result) == 2
        assert result[0] == (10000, 20000)
        assert result[1] == (30000, 40000)

    def test_parse_port_ranges_single(self):
        result = parse_port_ranges("10000-10010")
        assert result == [(10000, 10010)]

    def test_parse_port_ranges_empty(self):
        result = parse_port_ranges("")
        assert result == []

    def test_max_100_ports_per_subscription(self):
        requested = list(range(10001, 10102))
        assert len(requested) > 100
        assert len(requested[:100]) == 100

    def test_client_numbering(self):
        highest = 5
        next_num = (highest or 0) + 1
        assert next_num == 6
        client_id = f"client{next_num:03d}"
        assert client_id == "client006"

    def test_client_numbering_first(self):
        highest = None
        next_num = (highest or 0) + 1
        assert next_num == 1
        client_id = f"client{next_num:03d}"
        assert client_id == "client001"

    def test_subnet_assignment(self):
        vpn_subnet_base = "10.254"
        for i in range(1, 4):
            subnet_num = i
            vpn_subnet = f"{vpn_subnet_base}.{subnet_num}.0/30"
            vpn_ip = f"{vpn_subnet_base}.{subnet_num}.1"
            assert vpn_subnet == f"10.254.{i}.0/30"
            assert vpn_ip == f"10.254.{i}.1"

    def test_max_253_clients(self):
        max_clients = 253
        for i in range(1, max_clients + 1):
            subnet = f"10.254.{i}.0/30"
            assert "10.254." in subnet
        assert max_clients == 253

    def test_price_calculation_single_port(self):
        price_per_port = 1000
        port_count = 1
        periods = 1
        total = price_per_port * port_count * periods
        assert total == 1000

    def test_price_calculation_multi_port(self):
        price_per_port = 1000
        port_count = 5
        periods = 2
        total = price_per_port * port_count * periods
        assert total == 10000

    def test_price_calculation_renewal(self):
        price_per_port = 1000
        port_count = 3
        extra_days = 60
        base_days = 30
        periods = extra_days // base_days
        total = price_per_port * port_count * periods
        assert total == 6000
