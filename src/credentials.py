"""API credential loading for demo / prod.

Environment variables (a .env file is also read), PREFIX = DEMO or PROD:

    {PREFIX}_KEYID         API key ID (UUID shown on the Kalshi API keys page)
    {PREFIX}_KEYFILE       path to the private key .pem          -- or --
    {PREFIX}_PRIVATE_KEY   the PEM text itself (BEGIN/END lines included).
                           Literal "\\n" sequences and base64-wrapped PEM are
                           accepted, so the key fits in a single-line secret.

RSA keys sign with RSA-PSS/SHA-256; Ed25519 keys are also supported.
Nothing here ever prints or logs key material.
"""
import base64
import binascii
import os
from typing import Mapping, Optional, Tuple

from cryptography.hazmat.primitives import serialization

from src.clients import Environment, KalshiHttpClient


class CredentialsError(RuntimeError):
    pass


def parse_environment(name: str) -> Environment:
    name = (name or "demo").strip().lower()
    if name == "demo":
        return Environment.DEMO
    if name in ("prod", "production"):
        return Environment.PROD
    raise ValueError(f"unknown environment {name!r} (expected demo or prod)")


def _pem_bytes(raw: str) -> bytes:
    text = raw.strip().replace("\\n", "\n")
    if "-----BEGIN" not in text:
        try:
            text = base64.b64decode(text, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as e:
            raise CredentialsError("private key is neither PEM text nor base64-encoded "
                                   "PEM") from e
    return text.encode("utf-8")


def load_private_key_bytes(pem: bytes):
    try:
        return serialization.load_pem_private_key(pem, password=None)
    except (ValueError, TypeError) as e:
        raise CredentialsError(f"could not parse private key: {type(e).__name__}") from e


def mask_key_id(key_id: str) -> str:
    return f"{key_id[:4]}...{key_id[-4:]}" if key_id and len(key_id) > 8 else "****"


def load_credentials(environment: Environment,
                     env: Optional[Mapping[str, str]] = None) -> Tuple[str, object]:
    if env is None:
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            pass
        env = os.environ
    prefix = "DEMO" if environment == Environment.DEMO else "PROD"
    key_id = env.get(f"{prefix}_KEYID") or env.get(f"{prefix}_KEY_ID")
    inline = env.get(f"{prefix}_PRIVATE_KEY")
    keyfile = env.get(f"{prefix}_KEYFILE")

    missing = []
    if not key_id:
        missing.append(f"{prefix}_KEYID")
    if not inline and not keyfile:
        missing.append(f"{prefix}_PRIVATE_KEY (PEM text) or {prefix}_KEYFILE (path)")
    if missing:
        raise CredentialsError("missing credentials: " + ", ".join(missing))

    if inline:
        pem = _pem_bytes(inline)
    else:
        try:
            with open(keyfile, "rb") as f:
                pem = f.read()
        except OSError as e:
            raise CredentialsError(f"cannot read {prefix}_KEYFILE: {e.strerror}") from e
    return key_id.strip(), load_private_key_bytes(pem)


def make_client(environment: Environment, env: Optional[Mapping[str, str]] = None,
                **client_kwargs) -> KalshiHttpClient:
    key_id, private_key = load_credentials(environment, env)
    return KalshiHttpClient(key_id=key_id, private_key=private_key,
                            environment=environment, **client_kwargs)


def make_public_client(environment: Environment, **client_kwargs) -> KalshiHttpClient:
    """Unsigned client for the public market-data endpoints (markets, order
    books, trades, series, exchange status). Enough for market selection and
    a dry run against live books; placing orders needs credentials."""
    return KalshiHttpClient(key_id=None, private_key=None, environment=environment,
                            **client_kwargs)
