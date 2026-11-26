"""Exact UUID identity boundaries; no provider or private-account access."""
import pytest

from nexus import alpaca_activity_normalizer as activity
from nexus import alpaca_paper_inspection as inspection
from nexus import broker_fee_projection as projection
from nexus.broker_request_journal import BrokerRequestJournal


VISIBLE='01234567-89ab-cdef-0123-456789abcdef'
SUBSTITUTED='11234567-89ab-cdef-0123-456789abcdef'


class SwappedUuid(str):
    def replace(self,*args,**kwargs):return SUBSTITUTED


def test_polymorphic_uuid_cannot_substitute_account_or_broker_identity(tmp_path):
    forged=SwappedUuid(VISIBLE);path=tmp_path/'private'/'requests.sqlite3'
    with pytest.raises(inspection.PaperInspectionError):inspection._uuid(forged)
    with pytest.raises(ValueError,match='ALPACA_ACTIVITY_UUID_INVALID'):activity._uuid(forged)
    with pytest.raises(ValueError,match='INVALID_BROKER_PROJECTION_ORDER_ID'):projection.broker_id(forged)
    with pytest.raises(ValueError,match='INVALID_PAPER_ACCOUNT_SCOPE'):BrokerRequestJournal(path,forged)
    assert not path.parent.exists()
