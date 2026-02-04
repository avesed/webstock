"""JWT Key rotation utilities for smooth key transitions."""

import os
import secrets
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import settings


def generate_key() -> str:
    """Generate a new secure JWT secret key."""
    return secrets.token_hex(32)


def parse_key_list(keys_str: Optional[str]) -> list[str]:
    """Parse comma-separated key list."""
    if not keys_str:
        return []
    return [k.strip() for k in keys_str.split(",") if k.strip()]


def get_current_keys() -> dict:
    """Get current key configuration."""
    primary = settings.JWT_SECRET_KEY
    previous = parse_key_list(os.environ.get("JWT_SECRET_KEY_PREVIOUS", ""))
    
    return {
        "primary": primary[:8] + "..." + primary[-8:] if len(primary) > 20 else primary,
        "primary_length": len(primary),
        "previous_count": len(previous),
        "previous_keys": [k[:8] + "..." + k[-8:] if len(k) > 20 else k for k in previous],
    }


def rotate_keys():
    """
    Perform key rotation.
    
    Process:
    1. Generate new primary key
    2. Move current primary to previous keys list
    3. Output new configuration
    
    Users with valid tokens can still use them until they expire
    because previous keys are still accepted for verification.
    """
    current_primary = settings.JWT_SECRET_KEY
    current_previous = parse_key_list(os.environ.get("JWT_SECRET_KEY_PREVIOUS", ""))
    
    # Generate new key
    new_primary = generate_key()
    
    # Build new previous keys list (keep last 2 keys for safety)
    new_previous = [current_primary] + current_previous[:1]
    
    print("=" * 60)
    print("JWT Key Rotation")
    print("=" * 60)
    print(f"Timestamp: {datetime.now().isoformat()}")
    print()
    print("Current Configuration:")
    print(f"  Primary: {current_primary[:8]}...{current_primary[-8:]}")
    print(f"  Previous keys: {len(current_previous)}")
    for i, key in enumerate(current_previous, 1):
        print(f"    [{i}] {key[:8]}...{key[-8:]}")
    print()
    print("New Configuration:")
    print(f"  Primary: {new_primary[:8]}...{new_primary[-8:]}")
    print(f"  Previous keys: {len(new_previous)}")
    for i, key in enumerate(new_previous, 1):
        print(f"    [{i}] {key[:8]}...{key[-8:]}")
    print()
    print("Environment Variables to Update:")
    print("-" * 60)
    print(f"JWT_SECRET_KEY={new_primary}")
    print(f"JWT_SECRET_KEY_PREVIOUS={','.join(new_previous)}")
    print("-" * 60)
    print()
    print("Next Steps:")
    print("1. Update your .env file with the new values above")
    print("2. Restart the backend service")
    print("3. New tokens will use the new key")
    print("4. Existing tokens will continue to work until they expire")
    print("5. After 24-48 hours, you can remove old keys from PREVIOUS list")
    print()
    print("⚠️  IMPORTANT:")
    print("   - Save the new keys securely before restarting")
    print("   - Keep previous keys for at least the token expiry duration")
    print("   - In production, use a secrets manager (Vault, AWS KMS, etc.)")
    print("=" * 60)
    
    return {
        "new_primary": new_primary,
        "new_previous": new_previous,
    }


def validate_keys():
    """Validate current key configuration."""
    primary = settings.JWT_SECRET_KEY
    previous = parse_key_list(os.environ.get("JWT_SECRET_KEY_PREVIOUS", ""))
    
    issues = []
    
    # Check primary key
    if not primary:
        issues.append("❌ JWT_SECRET_KEY is not set")
    elif len(primary) < 32:
        issues.append(f"⚠️  JWT_SECRET_KEY is too short ({len(primary)} chars, recommend 64+)")
    
    if primary == "change-me-in-production-use-openssl-rand-hex-32":
        issues.append("❌ JWT_SECRET_KEY is using default/example value")
    
    # Check previous keys
    if previous:
        for i, key in enumerate(previous):
            if not key:
                issues.append(f"❌ Previous key [{i+1}] is empty")
            elif len(key) < 32:
                issues.append(f"⚠️  Previous key [{i+1}] is too short ({len(key)} chars)")
    
    print("=" * 60)
    print("JWT Key Validation")
    print("=" * 60)
    print(f"Timestamp: {datetime.now().isoformat()}")
    print()
    print("Current Keys:")
    print(f"  Primary: {primary[:8]}...{primary[-8:] if len(primary) > 16 else primary}")
    print(f"  Primary length: {len(primary)} chars")
    print(f"  Previous keys: {len(previous)}")
    print()
    
    if issues:
        print("Issues Found:")
        for issue in issues:
            print(f"  {issue}")
        print()
        print("❌ Validation FAILED")
    else:
        print("✅ All keys are valid")
        if not previous:
            print()
            print("ℹ️  No previous keys configured")
            print("   Run 'python rotate_keys.py --rotate' to set up key rotation")
    
    print("=" * 60)
    
    return len(issues) == 0


def emergency_revoke_all():
    """
    Emergency: Revoke all existing tokens by changing keys completely.
    
    ⚠️ This will force all users to re-login!
    Use only in case of suspected key compromise.
    """
    print("=" * 60)
    print("EMERGENCY: Complete Key Replacement")
    print("=" * 60)
    print()
    print("⚠️  WARNING: This will invalidate ALL existing tokens!")
    print("   All users will be forced to re-login.")
    print()
    
    confirm = input("Type 'EMERGENCY' to confirm: ")
    if confirm != "EMERGENCY":
        print("Cancelled.")
        return
    
    new_primary = generate_key()
    
    print()
    print("New Configuration:")
    print(f"  Primary: {new_primary[:8]}...{new_primary[-8:]}")
    print(f"  Previous keys: 0 (all old tokens invalidated)")
    print()
    print("Environment Variables:")
    print("-" * 60)
    print(f"JWT_SECRET_KEY={new_primary}")
    print("# Remove or comment out JWT_SECRET_KEY_PREVIOUS")
    print("-" * 60)
    print()
    print("🚨 ACTION REQUIRED:")
    print("1. Update .env file immediately")
    print("2. Restart backend service")
    print("3. All users will need to re-login")
    print("=" * 60)
    
    return {"new_primary": new_primary}


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="JWT Key Rotation Tool")
    parser.add_argument(
        "--rotate",
        action="store_true",
        help="Perform key rotation (smooth transition)",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate current key configuration",
    )
    parser.add_argument(
        "--emergency",
        action="store_true",
        help="Emergency: replace all keys (forces re-login)",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show current key status",
    )
    
    args = parser.parse_args()
    
    if not any([args.rotate, args.validate, args.emergency, args.status]):
        # Default: show status
        args.status = True
    
    if args.status:
        info = get_current_keys()
        print("=" * 60)
        print("JWT Key Status")
        print("=" * 60)
        print(f"Primary key: {info['primary']}")
        print(f"Key length: {info['primary_length']} chars")
        print(f"Previous keys: {info['previous_count']}")
        if info['previous_keys']:
            for i, key in enumerate(info['previous_keys'], 1):
                print(f"  [{i}] {key}")
        print("=" * 60)
        print()
        print("Commands:")
        print("  python rotate_keys.py --rotate    # Rotate keys")
        print("  python rotate_keys.py --validate  # Validate keys")
        print("  python rotate_keys.py --emergency # Emergency replacement")
    
    if args.validate:
        print()
        validate_keys()
    
    if args.rotate:
        print()
        rotate_keys()
    
    if args.emergency:
        print()
        emergency_revoke_all()
