"""`serve` exposes the axor-identity JWKS options (deploy wiring)."""
from __future__ import annotations

from lab_runner.cli import _build_parser


def test_serve_accepts_identity_flags() -> None:
    parser = _build_parser()
    args = parser.parse_args(
        ["serve", "--identity-jwks-url", "http://id.example/jwks",
         "--identity-issuer", "acme-identity"])
    assert args.identity_jwks_url == "http://id.example/jwks"
    assert args.identity_issuer == "acme-identity"

    file_args = parser.parse_args(["serve", "--identity-jwks-file", "/etc/jwks.json"])
    assert file_args.identity_jwks_file == "/etc/jwks.json"


def test_identity_flags_default_off() -> None:
    args = _build_parser().parse_args(["serve"])
    assert args.identity_jwks_url is None
    assert args.identity_jwks_file is None
    assert args.identity_issuer is None
