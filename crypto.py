"""
Fernet symmetric encryption for storing server API passwords in the DB.
The ENCRYPTION_KEY env var must be a valid Fernet key (32 url-safe base64 bytes).
Generate with: python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""
import logging
from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

_fernet: Fernet | None = None


def _get_fernet() -> Fernet | None:
    global _fernet
    if _fernet is None:
        from config import settings
        key = settings.encryption_key.strip()
        if not key:
            return None
        try:
            _fernet = Fernet(key.encode())
        except Exception as e:
            logger.warning(f"Invalid ENCRYPTION_KEY: {e}")
            return None
    return _fernet


def encrypt(plaintext: str) -> str:
    """Encrypt a string. Returns ciphertext or plaintext if no key configured."""
    f = _get_fernet()
    if f is None:
        return plaintext
    return f.encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    """Decrypt a string. Returns plaintext. Falls back to returning value as-is if decryption fails."""
    f = _get_fernet()
    if f is None:
        return ciphertext
    try:
        return f.decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        # Might be stored as plaintext (before encryption was enabled)
        return ciphertext


def generate_key() -> str:
    """Generate a new Fernet key — use once for ENCRYPTION_KEY in .env."""
    return Fernet.generate_key().decode()
