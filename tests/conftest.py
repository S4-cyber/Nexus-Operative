"""Legitimate local HTTP defaults for in-process clients, never a runtime bypass."""
import pytest
from starlette.testclient import TestClient


@pytest.fixture(autouse=True)
def local_test_transport(monkeypatch):
    original=TestClient.__init__
    def initialize(self,*args,**kwargs):
        kwargs.setdefault('base_url','http://127.0.0.1')
        kwargs.setdefault('client',('127.0.0.1',50000))
        original(self,*args,**kwargs)
    monkeypatch.setattr(TestClient,'__init__',initialize)
