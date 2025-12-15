import os
from typing import Optional
from bech32 import bech32_decode, convertbits, bech32_encode
from ecdsa import SigningKey, SECP256k1


class NostrKeyError(Exception):
    pass


def decode_nip19(nsec: str) -> str:
    hrp, data = bech32_decode(nsec)
    if hrp in {"nsec", "npub"} and data is not None:
        decoded = convertbits(data, 5, 8, False)
        if decoded is None:
            raise NostrKeyError("Failed to decode bech32 key")
        return bytes(decoded).hex()

    # Fallback: allow placeholder npub strings by decoding data without verifying checksum.
    if nsec.startswith("npub1"):
        try:
            sep = nsec.rfind("1")
            raw = nsec[sep + 1 :] if sep != -1 else ""
            # strip checksum (6 chars) if present
            payload = raw[:-6] if len(raw) > 6 else raw
            CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
            data5 = [CHARSET.find(c) for c in payload if CHARSET.find(c) != -1]
            decoded = convertbits(data5, 5, 8, False)
            if decoded:
                return bytes(decoded).hex()
        except Exception:
            pass
    raise NostrKeyError("Invalid NIP-19 key")


def encode_npub(pubkey_hex: str) -> str:
    data = convertbits(bytes.fromhex(pubkey_hex), 8, 5, True)
    if data is None:
        raise NostrKeyError("Failed to encode npub")
    return bech32_encode("npub", list(data))


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
