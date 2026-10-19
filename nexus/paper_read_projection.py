"""Bounded normalized-result fingerprints, never provider/source attestation.

The cooperating reader binds this digest alongside accepted response-byte
digests. No plaintext result, account identifier or response body is retained.
"""
import hashlib
import re

from nexus.alpaca_paper_contract import canonical

PROFILE = 'nexus.paper-normalized-read-result.v1'
MAX_BYTES = 65536
QUOTE_SCOPE = ('profile','account_sha256','quote','source','account_fields_sha256','read_timing')


def digest(operation, value):
    try:
        if operation in ('ACCOUNT_INVENTORY','BTC_ASSET_RULES'):
            if type(value) is not dict: raise ValueError
            projection = value
        elif operation == 'BTC_QUOTE_OBSERVATION':
            projection = {key:value[key] for key in QUOTE_SCOPE}
            # Finalization is sampled after RESULT commits and cannot be bound
            # by that earlier event. Existing quote replay validates it separately.
            projection['read_timing'] = {key:item for key,item in projection['read_timing'].items()
                if key != 'finalized_at'}
        elif operation == 'ACTIVITY_COLLECTION':
            if type(value) is not str or re.fullmatch('[0-9a-f]{64}',value) is None: raise ValueError
            projection = dict(normalized_collection_receipt_sha256=value)
        elif operation == 'ORIGINAL_ID_RECOVERY':
            if type(value) is not dict or set(value) != {'client_order_id','journal'}: raise ValueError
            projection = value
        else:
            raise ValueError
        encoded = canonical(dict(profile=PROFILE,operation=operation,projection=projection)).encode()
        if len(encoded) > MAX_BYTES: raise ValueError
        return hashlib.sha256(encoded).hexdigest()
    except (ValueError,TypeError,KeyError,AttributeError,OverflowError,RecursionError):
        raise ValueError('PAPER_READ_PROJECTION_INVALID') from None


def matches(record, operation, value):
    from nexus.paper_read_pacing import RECEIPT_PROFILE_V3
    if (type(record) is not dict or record.get('operation') != operation or
        record.get('outcome') != 'SUCCESS' or record.get('receipt_profile') != RECEIPT_PROFILE_V3 or
        record.get('normalized_result_bound') is not True):
        return False
    try:
        return record.get('result_projection_sha256') == digest(operation,value)
    except ValueError:
        return False
