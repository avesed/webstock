"""Quick fix for JWT signature verification issues."""

import os
import sys
from pathlib import Path

# Add paths
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))


def diagnose_jwt_issue():
    """Diagnose the JWT signature verification issue."""
    print("=" * 60)
    print("JWT Signature Verification Diagnostic")
    print("=" * 60)
    
    # Check environment variables
    env_key = os.environ.get("JWT_SECRET_KEY", "")
    env_previous = os.environ.get("JWT_SECRET_KEY_PREVIOUS", "")
    
    print(f"\n1. Environment Variables:")
    print(f"   JWT_SECRET_KEY: {env_key[:8]}...{env_key[-8:] if len(env_key) > 16 else env_key}" if env_key else "   JWT_SECRET_KEY: NOT SET")
    print(f"   JWT_SECRET_KEY_PREVIOUS: {'Set' if env_previous else 'Not set'}")
    
    # Check .env file
    env_file = Path(__file__).parent.parent / ".env"
    if env_file.exists():
        print(f"\n2. .env file content:")
        content = env_file.read_text()
        for line in content.split("\n"):
            if line.startswith("JWT_SECRET_KEY"):
                key = line.split("=", 1)[1] if "=" in line else ""
                print(f"   {line[:25]}...{key[-8:] if len(key) > 16 else key}" if key else f"   {line}")
    else:
        print(f"\n2. .env file: NOT FOUND")
    
    # Check if keys match
    if env_file.exists():
        for line in env_file.read_text().split("\n"):
            if line.startswith("JWT_SECRET_KEY=") and not line.startswith("JWT_SECRET_KEY_PREVIOUS"):
                file_key = line.split("=", 1)[1] if "=" in line else ""
                if file_key != env_key:
                    print(f"\n❌ MISMATCH DETECTED!")
                    print(f"   .env file key: {file_key[:8]}...{file_key[-8:]}")
                    print(f"   Environment key: {env_key[:8]}...{env_key[-8:]}")
                    print(f"\n   This means the backend is using a different key than expected!")
                    print(f"   You need to restart the backend service.")
                    return False
    
    print(f"\n3. Recommendations:")
    if not env_key or env_key == "change-me-in-production":
        print("   ❌ JWT_SECRET_KEY is not properly set!")
        print("   Run: python scripts/fix_jwt.py --generate")
    elif len(env_key) < 32:
        print("   ⚠️  JWT_SECRET_KEY is too short!")
        print("   Run: python scripts/fix_jwt.py --generate")
    else:
        print("   ✅ Key is properly configured")
    
    print(f"\n4. To fix the refresh issue:")
    print("   1. Clear browser cookies and localStorage")
    print("   2. Re-login to get fresh tokens")
    print("   3. Ensure backend has correct JWT_SECRET_KEY")
    print("   4. Restart backend if you changed .env")
    
    return True


def generate_and_set_key():
    """Generate a new JWT key and update .env file."""
    import secrets
    
    print("=" * 60)
    print("Generate New JWT Key")
    print("=" * 60)
    
    # Generate new key
    new_key = secrets.token_hex(32)
    print(f"\nGenerated key: {new_key[:8]}...{new_key[-8:]}")
    print(f"Key length: {len(new_key)} characters")
    
    # Update .env file
    env_file = Path(__file__).parent.parent / ".env"
    if env_file.exists():
        content = env_file.read_text()
        lines = content.split("\n")
        new_lines = []
        updated = False
        
        for line in lines:
            if line.startswith("JWT_SECRET_KEY=") and not line.startswith("JWT_SECRET_KEY_PREVIOUS"):
                # Store old key as previous for smooth transition
                old_key = line.split("=", 1)[1] if "=" in line else ""
                if old_key and old_key != "change-me-in-production":
                    new_lines.append(f"JWT_SECRET_KEY_PREVIOUS={old_key}")
                    print(f"Stored old key as previous: {old_key[:8]}...{old_key[-8:]}")
                
                new_lines.append(f"JWT_SECRET_KEY={new_key}")
                updated = True
            elif not line.startswith("JWT_SECRET_KEY_PREVIOUS="):
                new_lines.append(line)
        
        if not updated:
            new_lines.append(f"JWT_SECRET_KEY={new_key}")
        
        env_file.write_text("\n".join(new_lines))
        print(f"\n✅ Updated {env_file}")
    else:
        # Create new .env file
        env_content = f"""# JWT Configuration
JWT_SECRET_KEY={new_key}
# Previous keys for smooth rotation (comma-separated)
# JWT_SECRET_KEY_PREVIOUS=old-key-1,old-key-2
"""
        env_file.write_text(env_content)
        print(f"\n✅ Created {env_file}")
    
    print(f"\n📝 IMPORTANT:")
    print(f"   1. Restart backend: docker-compose restart backend")
    print(f"   2. Clear browser cookies and re-login")
    print(f"   3. Old tokens will still work until they expire (smooth transition)")
    
    return new_key


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Fix JWT signature issues")
    parser.add_argument("--diagnose", action="store_true", help="Diagnose current JWT configuration")
    parser.add_argument("--generate", action="store_true", help="Generate new JWT key")
    
    args = parser.parse_args()
    
    if args.generate:
        generate_and_set_key()
    else:
        diagnose_jwt_issue()
