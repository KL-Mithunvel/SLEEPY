"""
Local GeoIP resolution for the admin login-monitoring view — no external API
call, so visitor/owner IPs never leave the server. Backed by a DB-IP City
Lite .mmdb file (CC-BY 4.0, freely redistributable) downloaded at Docker
build time (see Dockerfile.backend) rather than committed to git — the
uncompressed database is ~127MB.

Public API:
    resolve(ip_address) -> (city: str | None, country: str | None)
        Never raises. Returns (None, None) if the database isn't present
        (e.g. local dev, where it's never downloaded) or the IP can't be
        resolved (private/reserved ranges, lookup miss, etc).
"""

import logging

import config

logger = logging.getLogger(__name__)

_reader = None
_load_attempted = False


def _get_reader():
    global _reader, _load_attempted
    if _load_attempted:
        return _reader
    _load_attempted = True
    try:
        import maxminddb
        _reader = maxminddb.open_database(config.GEOIP_DB_PATH)
        logger.info("GeoIP: loaded database at %s", config.GEOIP_DB_PATH)
    except Exception as exc:
        logger.warning("GeoIP: database unavailable (%s) — locations will show as unknown", exc)
        _reader = None
    return _reader


def resolve(ip_address: str | None) -> tuple[str | None, str | None]:
    if not ip_address:
        return None, None

    reader = _get_reader()
    if reader is None:
        return None, None

    try:
        result = reader.get(ip_address)
        if not result:
            return None, None
        city = (result.get("city") or {}).get("names", {}).get("en")
        country = (result.get("country") or {}).get("names", {}).get("en")
        return city, country
    except Exception as exc:
        logger.debug("GeoIP: lookup failed for %s: %s", ip_address, exc)
        return None, None
