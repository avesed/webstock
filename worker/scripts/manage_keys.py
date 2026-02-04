"""Management commands for JWT key operations."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Add paths
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from worker.tasks.key_rotation import (
    auto_rotate_jwt_keys,
    cleanup_old_jwt_keys,
    verify_jwt_key_rotation,
)


def main():
    parser = argparse.ArgumentParser(
        description="JWT Key Management CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python manage_keys.py rotate           # Rotate keys immediately
  python manage_keys.py verify           # Verify current key configuration
  python manage_keys.py cleanup          # Cleanup old keys
  python manage_keys.py status           # Show current status
        """
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")
    
    # Rotate command
    rotate_parser = subparsers.add_parser("rotate", help="Rotate JWT keys immediately")
    rotate_parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Skip confirmation prompt"
    )
    
    # Verify command
    subparsers.add_parser("verify", help="Verify key configuration")
    
    # Cleanup command
    cleanup_parser = subparsers.add_parser("cleanup", help="Cleanup old keys")
    cleanup_parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Skip confirmation prompt"
    )
    
    # Status command
    subparsers.add_parser("status", help="Show current key status")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    if args.command == "rotate":
        if not args.yes:
            confirm = input("This will rotate JWT keys. Existing tokens will still work. Continue? [y/N]: ")
            if confirm.lower() != "y":
                print("Cancelled.")
                return
        
        print("Rotating JWT keys...")
        result = auto_rotate_jwt_keys()
        print(json.dumps(result, indent=2))
        
        if result["status"] == "success":
            print("\n✅ Keys rotated successfully!")
            print("Remember to restart the backend service to apply new keys.")
            print("  docker-compose restart backend")
        else:
            print("\n❌ Rotation failed!")
            sys.exit(1)
    
    elif args.command == "verify":
        print("Verifying JWT key configuration...")
        result = verify_jwt_key_rotation()
        print(json.dumps(result, indent=2))
        
        if result["status"] == "ok":
            print("\n✅ Configuration is valid")
        elif result["status"] == "warning":
            print("\n⚠️  Configuration has issues")
            sys.exit(1)
        else:
            print("\n❌ Verification failed")
            sys.exit(1)
    
    elif args.command == "cleanup":
        if not args.yes:
            confirm = input("This will remove old JWT keys. Tokens signed with old keys will fail. Continue? [y/N]: ")
            if confirm.lower() != "y":
                print("Cancelled.")
                return
        
        print("Cleaning up old JWT keys...")
        result = cleanup_old_jwt_keys()
        print(json.dumps(result, indent=2))
        
        if result["status"] == "success":
            print("\n✅ Cleanup completed!")
        else:
            print("\n❌ Cleanup failed")
            sys.exit(1)
    
    elif args.command == "status":
        print("Current JWT Key Status")
        print("=" * 50)
        
        import os
        primary = os.environ.get("JWT_SECRET_KEY", "")
        previous = os.environ.get("JWT_SECRET_KEY_PREVIOUS", "")
        
        if primary:
            print(f"Primary Key: {primary[:8]}...{primary[-8:]}")
            print(f"Key Length: {len(primary)} chars")
        else:
            print("Primary Key: NOT SET")
        
        previous_keys = [k.strip() for k in previous.split(",") if k.strip()]
        print(f"Previous Keys: {len(previous_keys)}")
        for i, key in enumerate(previous_keys, 1):
            print(f"  [{i}] {key[:8]}...{key[-8:]}")
        
        print("=" * 50)


if __name__ == "__main__":
    main()
