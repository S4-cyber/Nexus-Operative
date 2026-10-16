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
_enabled=False

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
    if not isinstance(secret,str) or not 20<=len(secret)<=256 or not secret.startswith("sk-") or "\n" in secret or "\r" in secret:
        raise ValueError("INVALID_KEY_FORMAT")
    with _lock:
        vault=_vault()
        try:vault.set_password(SERVICE,ACCOUNT,secret)
        except Exception as exc:raise ValueError("VAULT_SAVE_FAILED") from None
        global _enabled
        _enabled=True
    return {"configured":True,"provider_connected":False,"stored_in":"WINDOWS_CREDENTIAL_MANAGER",
            "enabled_for_current_process":True,"paid_requests_sent":False,"live_orders_enabled":False}

def retrieve():
    with _lock:
        if not _enabled:return None
        try:return _vault().get_password(SERVICE,ACCOUNT)
        except Exception:return None

def configured():
    with _lock:
        try:return bool(_vault().get_password(SERVICE,ACCOUNT))
        except Exception:return False

def disconnect():
    with _lock:
        global _enabled
        _enabled=False
        try:
            vault=_vault()
            try:vault.delete_password(SERVICE,ACCOUNT)
            except Exception as exc:raise ValueError("VAULT_DELETE_FAILED") from None
        except ValueError:raise
    return {"configured":False,"provider_connected":False,"paid_requests_sent":False,
            "live_orders_enabled":False}
