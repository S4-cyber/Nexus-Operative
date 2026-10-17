"""Opt-in OS credential vault for local Windows desktop AI research.

No plaintext secret files, environment mutation, or secret-bearing responses.
Requires Windows Credential Manager via the installed keyring backend.
"""
import os
import threading
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
        if type(backend).__module__ != 'keyring.backends.Windows' or type(backend).__name__ != 'WinVaultKeyring':
            raise ValueError("TRUSTED_WINDOWS_VAULT_UNAVAILABLE")
        return backend  # Use the exact checked object, not a second backend dispatch.
    except (ImportError,RuntimeError) as exc:
        raise ValueError("TRUSTED_WINDOWS_VAULT_UNAVAILABLE") from exc

def validate_secret(secret):
    if not isinstance(secret,str) or not 20<=len(secret)<=256 or not secret.startswith("sk-") or any(not 33 <= ord(char) <= 126 for char in secret):
        raise ValueError("INVALID_KEY_FORMAT")
    return secret

def install(secret):
    global _observation
    validate_secret(secret)
    with _lock:
        _observation = None
        vault=_vault()
        try:
            vault.set_password(SERVICE,ACCOUNT,secret)
            if vault.get_password(SERVICE,ACCOUNT) != secret:
                raise ValueError('UNCONFIRMED_VAULT_SAVE')
        except Exception as exc:raise ValueError("VAULT_SAVE_FAILED") from None
        _observation = True
    return {"configured":True,"provider_connected":False,"stored_in":"WINDOWS_CREDENTIAL_MANAGER",
            "enabled_for_current_process":True,"paid_requests_sent":False,"live_orders_enabled":False}

def retrieve():
    global _observation
    with _lock:
        try:
            secret = _vault().get_password(SERVICE,ACCOUNT)
            if secret is not None: validate_secret(secret)
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
            try:
                if vault.get_password(SERVICE,ACCOUNT) is not None:
                    vault.delete_password(SERVICE,ACCOUNT)
                if vault.get_password(SERVICE,ACCOUNT) is not None:
                    raise ValueError('UNCONFIRMED_VAULT_REMOVAL')
            except Exception as exc:raise ValueError("VAULT_DELETE_FAILED") from None
        except ValueError:raise
        _observation = False
    return {"configured":False,"provider_connected":False,"paid_requests_sent":False,
            "live_orders_enabled":False}

