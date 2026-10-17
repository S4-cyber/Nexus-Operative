"""Fixed public-candle windows for explicit refresh opt-in; no account/provider IO."""
import json
import shutil
from urllib.request import Request
from nexus import market_hub as hub
from nexus.historical_candles import fetch_candles
from nexus.public_data import _opener

SETTLE_SECONDS=5
MAX_SOURCE_REQUESTS=12


def preflight():
    status=hub.status()
    if (status['bars']+150>hub.MAX_BARS or status['conflicts']>=hub.MAX_CONFLICTS
            or status['database_bytes']>=hub.MAX_DB_BYTES
            or status['artifact_count']+status['unfinished_artifacts']+2>hub.MAX_ARTIFACTS
            or status['artifact_bytes']+2*hub.MAX_PACK_BYTES>hub.MAX_ARTIFACT_BYTES
            or shutil.disk_usage(hub.root()).free<256*1024*1024+2*hub.MAX_PACK_BYTES):
        raise ValueError('REFRESH_COLLECTION_CAPACITY')


def read_window(product,g,end,transport=None):
    if transport is None:
        def transport(url):
            with _opener.open(Request(url,headers={'Accept':'application/json','User-Agent':'NexusOperative-HubRefresh/1.0'}),timeout=5) as response:
                if response.status!=200 or response.geturl()!=url:raise ValueError('PUBLIC_RESPONSE_IDENTITY')
                raw=response.read(131073)
                if len(raw)>131072:raise ValueError('PUBLIC_RESPONSE_SIZE')
                return json.loads(raw)
    rows=fetch_candles(product,g,now=end,transport=transport)
    return [r for r in rows if end-150*g<=r['starts_at']<end]
