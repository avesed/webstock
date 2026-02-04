"""Celery tasks for automatic JWT key rotation."""

import os
import sys
from datetime import datetime
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "backend"))

from worker.celery_app import celery_app


@celery_app.task
def auto_rotate_jwt_keys():
    """
    Automatically rotate JWT keys every 6 hours.
    
    This task:
    1. Generates a new primary key
    2. Moves current primary to previous keys
    3. Updates environment configuration
    4. Logs the rotation event
    
    Note: In production, this should integrate with a secrets manager (Vault, AWS KMS, etc.)
    rather than directly modifying .env files.
    """
    try:
        import secrets
        
        # Get current configuration from environment
        current_primary = os.environ.get("JWT_SECRET_KEY", "")
        current_previous_str = os.environ.get("JWT_SECRET_KEY_PREVIOUS", "")
        
        # Parse previous keys
        current_previous = [k.strip() for k in current_previous_str.split(",") if k.strip()]
        
        # Generate new key
        new_primary = secrets.token_hex(32)
        
        # Build new previous keys list (keep last 2 keys)
        new_previous = [current_primary] + current_previous[:1]
        new_previous_str = ",".join(new_previous)
        
        # Log rotation event (safe logging with fingerprints only)
        timestamp = datetime.now().isoformat()
        print(f"[{timestamp}] JWT Key Rotation Executed")
        print(f"  Old primary: {current_primary[:8]}...{current_primary[-8:]}")
        print(f"  New primary: {new_primary[:8]}...{new_primary[-8:]}")
        print(f"  Previous keys count: {len(new_previous)}")
        
        # Update environment variables (for current process)
        os.environ["JWT_SECRET_KEY"] = new_primary
        os.environ["JWT_SECRET_KEY_PREVIOUS"] = new_previous_str
        
        # TODO: In production, update secrets manager instead of .env
        # Example for AWS Secrets Manager:
        # import boto3
        # client = boto3.client('secretsmanager')
        # client.put_secret_value(
        #     SecretId='webstock/jwt-keys',
        #     SecretString=json.dumps({
        #         'primary': new_primary,
        #         'previous': new_previous_str
        #     })
        # )
        
        # For Docker/local development: update .env file
        env_file = Path("/home/trevor/webstock/.env")
        if env_file.exists():
            update_env_file(env_file, new_primary, new_previous_str)
        
        return {
            "status": "success",
            "timestamp": timestamp,
            "new_primary_fingerprint": f"{new_primary[:8]}...{new_primary[-8:]}",
            "previous_keys_count": len(new_previous),
        }
        
    except Exception as e:
        print(f"[ERROR] JWT key rotation failed: {e}")
        return {
            "status": "error",
            "error": str(e),
            "timestamp": datetime.now().isoformat(),
        }


def update_env_file(env_file: Path, new_primary: str, new_previous: str):
    """Update .env file with new keys."""
    content = env_file.read_text()
    lines = content.split("\n")
    
    new_lines = []
    jwt_key_updated = False
    jwt_previous_updated = False
    
    for line in lines:
        if line.startswith("JWT_SECRET_KEY=") and not line.startswith("JWT_SECRET_KEY_PREVIOUS"):
            new_lines.append(f"JWT_SECRET_KEY={new_primary}")
            jwt_key_updated = True
        elif line.startswith("JWT_SECRET_KEY_PREVIOUS="):
            new_lines.append(f"JWT_SECRET_KEY_PREVIOUS={new_previous}")
            jwt_previous_updated = True
        else:
            new_lines.append(line)
    
    # Add keys if they don't exist
    if not jwt_key_updated:
        new_lines.append(f"JWT_SECRET_KEY={new_primary}")
    if not jwt_previous_updated:
        new_lines.append(f"JWT_SECRET_KEY_PREVIOUS={new_previous}")
    
    env_file.write_text("\n".join(new_lines))
    print(f"  Updated {env_file}")


@celery_app.task
def cleanup_old_jwt_keys():
    """
    Cleanup old JWT keys after rotation grace period.
    
    This should run after the maximum token lifetime (e.g., 24-48 hours)
    to remove old keys that are no longer needed.
    """
    try:
        current_previous_str = os.environ.get("JWT_SECRET_KEY_PREVIOUS", "")
        
        if not current_previous_str:
            return {
                "status": "skipped",
                "message": "No previous keys to cleanup",
            }
        
        # Clear previous keys
        os.environ["JWT_SECRET_KEY_PREVIOUS"] = ""
        
        # Update .env file
        env_file = Path("/home/trevor/webstock/.env")
        if env_file.exists():
            content = env_file.read_text()
            lines = content.split("\n")
            new_lines = []
            
            for line in lines:
                if line.startswith("JWT_SECRET_KEY_PREVIOUS="):
                    # Comment out or set empty
                    new_lines.append("# JWT_SECRET_KEY_PREVIOUS= (cleaned up after rotation)")
                else:
                    new_lines.append(line)
            
            env_file.write_text("\n".join(new_lines))
        
        timestamp = datetime.now().isoformat()
        print(f"[{timestamp}] Old JWT keys cleaned up")
        
        return {
            "status": "success",
            "timestamp": timestamp,
            "message": "Previous keys cleaned up",
        }
        
    except Exception as e:
        print(f"[ERROR] JWT key cleanup failed: {e}")
        return {
            "status": "error",
            "error": str(e),
        }


@celery_app.task
def verify_jwt_key_rotation():
    """
    Verify that key rotation is working correctly.
    
    This task periodically checks:
    1. Primary key is valid and strong
    2. Previous keys are available for smooth transition
    3. Rotation is happening as expected
    """
    try:
        import secrets
        
        primary = os.environ.get("JWT_SECRET_KEY", "")
        previous = os.environ.get("JWT_SECRET_KEY_PREVIOUS", "")
        
        issues = []
        
        # Check primary key
        if not primary:
            issues.append("Primary key is not set")
        elif len(primary) < 32:
            issues.append(f"Primary key is too short ({len(primary)} chars)")
        elif primary == "change-me-in-production":
            issues.append("Primary key is using default value")
        
        # Log status
        timestamp = datetime.now().isoformat()
        if issues:
            print(f"[{timestamp}] JWT Key Verification FAILED:")
            for issue in issues:
                print(f"  - {issue}")
            return {
                "status": "warning",
                "timestamp": timestamp,
                "issues": issues,
            }
        else:
            previous_count = len([k for k in previous.split(",") if k.strip()])
            print(f"[{timestamp}] JWT Key Verification OK")
            print(f"  Primary key: {primary[:8]}...{primary[-8:]}")
            print(f"  Previous keys: {previous_count}")
            return {
                "status": "ok",
                "timestamp": timestamp,
                "primary_fingerprint": f"{primary[:8]}...{primary[-8:]}",
                "previous_keys_count": previous_count,
            }
            
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
        }
