import argparse
import secrets
import sys


def generate_admin_token(entropy_bytes: int = 32) -> str:
    """Generate a strong admin token suitable for ADMIN_TOKEN."""

    return secrets.token_urlsafe(entropy_bytes)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Generate an ADMIN_TOKEN for the Imprint admin console.")
    parser.add_argument(
        "--bytes",
        type=int,
        default=32,
        help="Entropy in bytes to feed into token_urlsafe (default: 32, ~43 characters).",
    )
    args = parser.parse_args(argv)

    token = generate_admin_token(args.bytes)
    print("Generated admin token:")
    print()
    print(token)
    print()
    print("Copy this value into your environment (e.g. ADMIN_TOKEN in .env) and restart the server.")


if __name__ == "__main__":  # pragma: no cover - convenience script
    main(sys.argv[1:])
