"""
Unit tests for geoip_lookup.py. The real .mmdb is a build-time download
(~127MB, never present in dev/CI), so these test the graceful-degradation
path plus the result-parsing logic against a mocked reader.
"""

import geoip_lookup


def test_resolve_returns_none_for_empty_ip():
    assert geoip_lookup.resolve(None) == (None, None)
    assert geoip_lookup.resolve("") == (None, None)


def test_resolve_returns_none_when_database_missing(monkeypatch):
    # No real .mmdb in this environment — _get_reader() must fail closed,
    # not raise, and cache that failure rather than retrying every call.
    monkeypatch.setattr(geoip_lookup, "_reader", None)
    monkeypatch.setattr(geoip_lookup, "_load_attempted", False)
    assert geoip_lookup.resolve("8.8.8.8") == (None, None)


def test_resolve_parses_city_and_country_from_reader(monkeypatch):
    class _FakeReader:
        def get(self, ip):
            return {
                "city": {"names": {"en": "Mumbai"}},
                "country": {"names": {"en": "India"}},
            }

    monkeypatch.setattr(geoip_lookup, "_get_reader", lambda: _FakeReader())
    assert geoip_lookup.resolve("1.2.3.4") == ("Mumbai", "India")


def test_resolve_handles_missing_fields_in_result(monkeypatch):
    class _FakeReader:
        def get(self, ip):
            return {}

    monkeypatch.setattr(geoip_lookup, "_get_reader", lambda: _FakeReader())
    assert geoip_lookup.resolve("1.2.3.4") == (None, None)


def test_resolve_handles_lookup_miss(monkeypatch):
    class _FakeReader:
        def get(self, ip):
            return None

    monkeypatch.setattr(geoip_lookup, "_get_reader", lambda: _FakeReader())
    assert geoip_lookup.resolve("10.0.0.1") == (None, None)
