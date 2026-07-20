"""safe_open enforces the SSRF guard end-to-end (review r11 P1).

ssrf_check on its own only validates caller-supplied IP strings — it never
connects, so the IP it blesses and the IP a later fetcher dials are unrelated
(classic DNS-rebinding gap). safe_open closes it: it resolves DNS itself,
validates every address, connects pinned to a validated address (the HTTP
library can't re-resolve), keeps Host/SNI, and re-validates every redirect.
Tests inject the resolver and connector so no real network is touched.
"""

from __future__ import annotations

import unittest

from lab_endpoint import safe_open
from lab_endpoint.errors import UnsafeEndpoint

PUBLIC = "93.184.216.34"


class _Resp:
    def __init__(self, status=200, location=None):
        self.status = status
        self._location = location
        self.closed = False

    def getheader(self, name):
        return self._location if name == "Location" else None

    def close(self):
        self.closed = True


class TestSafeOpen(unittest.TestCase):
    def test_resolves_and_connects_pinned_to_the_validated_ip(self) -> None:
        seen = {}

        def resolve(host, port):
            return [PUBLIC]

        def connect(scheme, host, ip, port, path, timeout):
            seen.update(scheme=scheme, host=host, ip=ip, port=port, path=path)
            return _Resp(200)

        resp = safe_open("https://api.example.com/run", resolve=resolve, connect=connect)
        self.assertEqual(resp.status, 200)
        # the connection was pinned to the address WE validated, with the original
        # host preserved for Host/SNI
        self.assertEqual(seen["ip"], PUBLIC)
        self.assertEqual(seen["host"], "api.example.com")
        self.assertEqual(seen["port"], 443)

    def test_a_name_that_resolves_to_private_is_refused_before_connect(self) -> None:
        connected = []

        def resolve(host, port):
            return ["10.0.0.5"]  # rebinding: public-looking name → private address

        def connect(*a):
            connected.append(a)
            return _Resp(200)

        with self.assertRaises(UnsafeEndpoint):
            safe_open("https://evil.example/x", resolve=resolve, connect=connect)
        self.assertEqual(connected, [])  # never dialed the private address

    def test_any_private_address_in_the_set_rejects(self) -> None:
        # rebinding via a mixed A-record set: one public, one private → reject
        def resolve(host, port):
            return [PUBLIC, "127.0.0.1"]

        with self.assertRaises(UnsafeEndpoint):
            safe_open("https://mixed.example/x", resolve=resolve, connect=lambda *a: _Resp(200))

    def test_redirect_to_a_private_address_is_re_checked_and_refused(self) -> None:
        hops = []

        def resolve(host, port):
            hops.append(host)
            return [PUBLIC] if host == "public.example" else ["169.254.169.254"]

        def connect(scheme, host, ip, port, path, timeout):
            if host == "public.example":
                return _Resp(302, location="http://metadata.internal/latest/meta-data")
            return _Resp(200)  # should never be reached

        with self.assertRaises(UnsafeEndpoint):
            safe_open("https://public.example/start", resolve=resolve, connect=connect)
        # the redirect target WAS re-resolved and re-validated (and rejected)
        self.assertIn("metadata.internal", hops)

    def test_follows_a_safe_redirect(self) -> None:
        def resolve(host, port):
            return [PUBLIC]

        def connect(scheme, host, ip, port, path, timeout):
            if path.startswith("/start"):
                return _Resp(302, location="https://public.example/final")
            return _Resp(200)

        resp = safe_open("https://public.example/start", resolve=resolve, connect=connect)
        self.assertEqual(resp.status, 200)

    def test_too_many_redirects_is_refused(self) -> None:
        def connect(scheme, host, ip, port, path, timeout):
            return _Resp(302, location="https://public.example/loop")

        with self.assertRaises(UnsafeEndpoint):
            safe_open("https://public.example/loop", max_redirects=2,
                      resolve=lambda h, p: [PUBLIC], connect=connect)


if __name__ == "__main__":
    unittest.main()
