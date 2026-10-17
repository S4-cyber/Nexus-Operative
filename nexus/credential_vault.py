"""Opt-in OS credential vault for local Windows desktop AI research.

No plaintext secret files, environment mutation, or secret-bearing responses.
Requires Windows Credential Manager via the installed keyring backend.
"""
import os
import threading
from fastapi import HTTPException
SERVICE="NexusOperativeResearch"
ACCOUNT="OpenAIAdvisoryKey"
_lock=threading.RLock()
_observation = None  # Process-local hint only; startup never inspects the OS store.

def local_status():
    """Passive metadata only. External changes/restart require an explicit action."""
    # One immutable hint read; never wait behind a potentially slow OS operation.
    observed = _observation
    return dict(configured=observed is True,
                state='NOT_CHECKED' if observed is None else 'OBSERVED_PRESENT' if observed else 'OBSERVED_ABSENT',
                current_vault_checked=False, scope='PROCESS_LOCAL_LAST_EXPLICIT_OBSERVATION')

def _vault():
    if os.name!="nt":
        raise ValueError("SECURE_VAULT_WINDOWS_ONLY")
    try:
        import keyring
        backend=keyring.get_keyring()
        # Explicitly disallow plaintext or third-party fallback keyrings.
        if not type(backend).__module__.startswith("keyring.backends.Windows"):
            raise ValueError("TRUSTED_WINDOWS_VAULT_UNAVAILABLE")
        return keyring
    except (ImportError,RuntimeError) as exc:
        raise ValueError("TRUSTED_WINDOWS_VAULT_UNAVAILABLE") from exc

def install(secret):
    global _observation
    if not isinstance(secret,str) or not 20<=len(secret)<=256 or not secret.startswith("sk-") or "\n" in secret or "\r" in secret:
        raise ValueError("INVALID_KEY_FORMAT")
    with _lock:
        _observation = None
        vault=_vault()
        try:vault.set_password(SERVICE,ACCOUNT,secret)
        except Exception as exc:raise ValueError("VAULT_SAVE_FAILED") from None
        _observation = True
    return {"configured":True,"provider_connected":False,"stored_in":"WINDOWS_CREDENTIAL_MANAGER",
            "enabled_for_current_process":True,"paid_requests_sent":False,"live_orders_enabled":False}

def retrieve():
    global _observation
    with _lock:
        try:
            secret = _vault().get_password(SERVICE,ACCOUNT)
            _observation = bool(secret)
            return secret
        except Exception:
            _observation = None
            return None

def configured():
    # Explicit legacy inspection; passive callers use local_status instead.
    return bool(retrieve())

def disconnect():
    global _observation
    with _lock:
        _observation = None
        try:
            vault=_vault()
            try:vault.delete_password(SERVICE,ACCOUNT)
            except Exception as exc:raise ValueError("VAULT_DELETE_FAILED") from None
        except ValueError:raise
        _observation = False
    return {"configured":False,"provider_connected":False,"paid_requests_sent":False,
            "live_orders_enabled":False}

