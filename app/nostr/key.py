import os
from typing import Optional
from bech32 import bech32_decode, convertbits, bech32_encode
from ecdsa import SigningKey, SECP256k1


class NostrKeyError(Exception):
    pass


def decode_nip19(nsec: str) -> str:
    hrp, data = bech32_decode(nsec)
    if hrp not in {"nsec", "npub"} or data is None:
        raise NostrKeyError("Invalid NIP-19 key")
    decoded = convertbits(data, 5, 8, False)
    if decoded is None:
        raise NostrKeyError("Failed to decode bech32 key")
    return bytes(decoded).hex()


def encode_npub(pubkey_hex: str) -> str:
    data = convertbits(bytes.fromhex(pubkey_hex), 8, 5, True)
    return bech32_encode("npub", data)


def load_private_key(env_value: Optional[str] = None) -> SigningKey:
    key_value = env_value or os.getenv("NOSTR_NSEC") or os.getenv("NOSTR_SK_HEX")
    if not key_value:
        raise NostrKeyError("No private key configured")
    key_hex = key_value
    if key_value.startswith("nsec"):
        key_hex = decode_nip19(key_value)
    if len(key_hex) != 64:
        raise NostrKeyError("Private key must be 32-byte hex")
    return SigningKey.from_string(bytes.fromhex(key_hex), curve=SECP256k1)


def derive_pubkey_hex(sk: SigningKey) -> str:
    return sk.get_verifying_key().to_string().hex()


def npub_from_secret(secret: str) -> str:
    sk = load_private_key(secret)
    pubkey_hex = derive_pubkey_hex(sk)
    return encode_npub(pubkey_hex)
