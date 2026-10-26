"""Local shared-hub foundation: canonical public bars, conflict quarantine, sealed research packs.

No cloud transport, authentication claim, credentials or order authority.
"""
from contextlib import closing
from decimal import Decimal,InvalidOperation
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import time
import tempfile
import zlib
from nexus.public_data import PRODUCTS
from nexus.historical_candles import GRANULARITIES

MAX_BARS=250000
MAX_CONFLICTS=5000
MAX_DB_BYTES=128*1024*1024
MAX_RAW_BYTES=4*1024*1024
MAX_PACK_BYTES=2*1024*1024
MAX_ARTIFACT_BYTES=32*1024*1024
MAX_ARTIFACTS=64
DB_BUSY_TIMEOUT_MS=10000
SCHEMA='nexus.market-hub.snapshot.v1'


def root():return Path(os.environ.get('NEXUS_PORTABLE_DATA',str(Path.home()/'.nexus-operative')))/'matrix_hub'
def encoded(value):return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def digest(value):return hashlib.sha256(value).hexdigest()


def number(value,zero=False):
    if isinstance(value,bool) or not isinstance(value,(str,int,float)):raise ValueError('HUB_INVALID_NUMBER')
    try:n=Decimal(str(value))
    except (InvalidOperation,ValueError):raise ValueError('HUB_INVALID_NUMBER')
    if not n.is_finite() or n<0 or (not zero and n==0) or n>Decimal('1e12') or n.as_tuple().exponent < -18:
        raise ValueError('HUB_NUMBER_OUTSIDE_BOUNDS')
    text=format(n,'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def normalize(product,granularity,row,now):
    if product not in PRODUCTS or type(granularity) is not int or granularity not in GRANULARITIES:raise ValueError('HUB_MARKET_SCOPE')
    if not isinstance(row,dict) or set(row)!={'starts_at','low','high','open','close','volume'}:raise ValueError('HUB_PUBLIC_FIELDS_ONLY')
    start=row['starts_at']
    if type(start) is not int or start<0 or start%granularity or start+granularity>now:raise ValueError('HUB_UNCLOSED_OR_UNALIGNED_BAR')
    values={key:number(row[key],zero=key=='volume') for key in ('low','high','open','close','volume')}
    low,high=Decimal(values['low']),Decimal(values['high'])
    if low>high or any(not low<=Decimal(values[k])<=high for k in ('open','close')):raise ValueError('HUB_INCONSISTENT_OHLC')
    return dict(venue='coinbase_exchange_public',product=product,granularity=granularity,starts_at=start,**values)


def unpack(data):
    if len(data)>MAX_PACK_BYTES:raise ValueError('HUB_PACK_SIZE_LIMIT')
    decoder=zlib.decompressobj()
    try:raw=decoder.decompress(data,MAX_RAW_BYTES+1)
    except zlib.error:raise ValueError('HUB_INVALID_COMPRESSED_DATA')
    if len(raw)>MAX_RAW_BYTES or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:raise ValueError('HUB_INVALID_OR_OVERSIZED_PACK')
    return raw


def _begin_immediate(db):
    try:db.execute('BEGIN IMMEDIATE')
    except sqlite3.OperationalError as error:
        if getattr(error,'sqlite_errorcode',None) in (sqlite3.SQLITE_BUSY,sqlite3.SQLITE_LOCKED) or any(word in str(error).lower() for word in ('busy','locked')):
            raise ValueError('HUB_DATABASE_BUSY') from error
        raise


def _db():
    root().mkdir(parents=True,exist_ok=True)
    if shutil.disk_usage(root()).free<256*1024*1024:raise ValueError('HUB_DISK_RESERVE_LOW')
    path=root()/'hub.sqlite3'
    if path.exists() and path.stat().st_size>MAX_DB_BYTES:raise ValueError('HUB_DATABASE_BYTE_CAP')
    db=sqlite3.connect(path,timeout=DB_BUSY_TIMEOUT_MS/1000)
    try:
        db.execute('PRAGMA busy_timeout='+str(DB_BUSY_TIMEOUT_MS))
        db.execute('PRAGMA synchronous=FULL');size=db.execute('PRAGMA page_size').fetchone()[0]
        db.execute('PRAGMA max_page_count='+str(max(1,MAX_DB_BYTES//size)))
        _begin_immediate(db)
        db.execute('CREATE TABLE IF NOT EXISTS bars(product TEXT,g INTEGER,at INTEGER,payload BLOB NOT NULL,digest TEXT NOT NULL,first_seen REAL NOT NULL,PRIMARY KEY(product,g,at))')
        db.execute('CREATE TABLE IF NOT EXISTS conflicts(product TEXT,g INTEGER,at INTEGER,digest TEXT,payload BLOB NOT NULL,first_seen REAL NOT NULL,PRIMARY KEY(product,g,at,digest))')
        db.execute('CREATE TABLE IF NOT EXISTS generations(product TEXT,g INTEGER,version INTEGER NOT NULL,PRIMARY KEY(product,g))')
        db.commit()
        return db
    except BaseException:db.close();raise


def ingest(product,granularity,rows,now=None):
    now=time.time() if now is None else now
    if type(now) not in (int,float) or not math.isfinite(now) or now<=0:raise ValueError('HUB_INVALID_RECEIPT_TIME')
    if not isinstance(rows,list) or not 1<=len(rows)<=1000 or len(encoded(rows))>512*1024:raise ValueError('HUB_BATCH_LIMIT')
    normalized=[normalize(product,granularity,row,now) for row in rows]
    inserted=duplicates=quarantined=0
    with closing(_db()) as db,db:
        _begin_immediate(db)
        count=db.execute('SELECT COUNT(*) FROM bars').fetchone()[0]
        conflicts=db.execute('SELECT COUNT(*) FROM conflicts').fetchone()[0]
        for row in normalized:
            raw=encoded(row);sha=digest(raw);key=(product,granularity,row['starts_at'])
            prior=db.execute('SELECT digest FROM bars WHERE product=? AND g=? AND at=?',key).fetchone()
            if prior and prior[0]==sha:duplicates+=1;continue
            if prior:
                if db.execute('SELECT 1 FROM conflicts WHERE product=? AND g=? AND at=? AND digest=?',(*key,sha)).fetchone():duplicates+=1;continue
                if conflicts>=MAX_CONFLICTS:raise ValueError('HUB_CONFLICT_CAP')
                db.execute('INSERT INTO conflicts VALUES(?,?,?,?,?,?)',(*key,sha,zlib.compress(raw,9),now))
                conflicts+=1;quarantined+=1
            else:
                if count>=MAX_BARS:raise ValueError('HUB_BAR_CAP')
                db.execute('INSERT INTO bars VALUES(?,?,?,?,?,?)',(*key,zlib.compress(raw,9),sha,now));count+=1;inserted+=1
        if inserted or quarantined:db.execute('INSERT INTO generations VALUES(?,?,1) ON CONFLICT(product,g) DO UPDATE SET version=version+1',(product,granularity))
    return dict(inserted=inserted,duplicates=duplicates,quarantined=quarantined,cloud_connected=False,trade_approval=False)


def status():
    path=root()/'hub.sqlite3';bars=conflicts=0
    if path.exists():
        with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            bars=db.execute('SELECT COUNT(*) FROM bars').fetchone()[0];conflicts=db.execute('SELECT COUNT(*) FROM conflicts').fetchone()[0]
    packs=[p for p in (root()/'objects').iterdir() if p.is_file()] if (root()/'objects').exists() else []
    return dict(mode='LOCAL_CONGLOMERATE_HUB_FOUNDATION',bars=bars,conflicts=conflicts,database_bytes=path.stat().st_size if path.exists() else 0,
        bar_limit=MAX_BARS,database_byte_limit=MAX_DB_BYTES,artifact_count=sum(p.suffix=='.nhub' for p in packs),unfinished_artifacts=sum(p.suffix!='.nhub' for p in packs),artifact_bytes=sum(p.stat().st_size for p in packs),artifact_byte_limit=MAX_ARTIFACT_BYTES,
        cloud_connected=False,source_verified=False,provider_models_connected=False,live_orders_enabled=False,
        note='Public market records only. One canonical venue/product/interval/time identity; alternate content is quarantined. Hashes are integrity checks, not source attestation. No shared cloud service is deployed.')


def snapshot(product,granularity=3600,limit=1000):
    if product not in PRODUCTS or type(granularity) is not int or granularity not in GRANULARITIES or type(limit) is not int or not 2<=limit<=10000:raise ValueError('HUB_SNAPSHOT_SCOPE')
    path=root()/'hub.sqlite3'
    if not path.exists():raise ValueError('HUB_NO_DATA')
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db,db:
        db.execute('BEGIN')
        found=db.execute('SELECT at,payload,digest,first_seen FROM bars WHERE product=? AND g=? ORDER BY at DESC LIMIT ?',(product,granularity,limit)).fetchall()
        if not found:raise ValueError('HUB_NO_DATA')
        version=db.execute('SELECT version FROM generations WHERE product=? AND g=?',(product,granularity)).fetchone()[0]
        conflicted=db.execute('SELECT COUNT(*) FROM conflicts WHERE product=? AND g=? AND at>=? AND at<=?',(product,granularity,found[-1][0],found[0][0])).fetchone()[0]
    records=[]
    for at,packed,sha,seen in reversed(found):
        raw=unpack(packed)
        if digest(raw)!=sha:raise ValueError('HUB_RECORD_INTEGRITY')
        row=json.loads(raw)
        normalized=normalize(product,granularity,{k:row[k] for k in ('starts_at','low','high','open','close','volume')},seen)
        if row!=normalized or row['starts_at']!=at:raise ValueError('HUB_RECORD_IDENTITY')
        records.append({**row,'first_seen_at':seen})
    issues=[]
    if conflicted:issues.append('CONFLICTING_SOURCE_VERSIONS')
    if any(b['starts_at']-a['starts_at']!=granularity for a,b in zip(records,records[1:])):issues.append('MISSING_BAR_INTERVAL')
    return dict(schema=SCHEMA,product=product,granularity=granularity,generation=version,records=records,issues=issues,
        source='SINGLE_SOURCE_PUBLIC_UNATTESTED',availability_scope='FIRST_HUB_RECEIPT_NOT_HISTORICAL_POINT_IN_TIME_ATTESTATION')


def seal(document):
    raw=encoded(document)
    if len(raw)>MAX_RAW_BYTES:raise ValueError('HUB_ARTIFACT_RAW_CAP')
    sha=digest(raw);packed=zlib.compress(raw,9)
    if len(packed)>MAX_PACK_BYTES:raise ValueError('HUB_ARTIFACT_PACK_CAP')
    directory=root()/'objects';path=directory/(sha+'.nhub')
    # Serialize artifact quota/publication across local processes with the same DB lease.
    with closing(_db()) as db,db:
        db.execute('BEGIN IMMEDIATE');directory.mkdir(exist_ok=True)
        if path.exists():
            with path.open('rb') as source:existing_raw=unpack(source.read(MAX_PACK_BYTES+1))
            if existing_raw!=raw:raise ValueError('HUB_EXISTING_ARTIFACT_CORRUPT')
        else:
            existing=[p for p in directory.iterdir() if p.is_file()]
            if len(existing)>=MAX_ARTIFACTS or sum(p.stat().st_size for p in existing)+len(packed)>MAX_ARTIFACT_BYTES:raise ValueError('HUB_ARTIFACT_CAP')
            if shutil.disk_usage(directory).free<256*1024*1024+len(packed):raise ValueError('HUB_DISK_RESERVE_LOW')
            # Atomic replacement of a fully synced temporary file; never publish partial bytes.
            fd,name=tempfile.mkstemp(prefix=sha+'-',suffix='.tmp',dir=directory);temporary=Path(name)
            try:
                with os.fdopen(fd,'wb') as output:output.write(packed);output.flush();os.fsync(output.fileno())
                temporary.chmod(0o444);os.replace(temporary,path)
                if hasattr(os,'O_DIRECTORY'):
                    directory_fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY)
                    try:os.fsync(directory_fd)
                    finally:os.close(directory_fd)
            finally:
                if temporary.exists():temporary.chmod(0o600);temporary.unlink()
    with path.open('rb') as source:actual=source.read(MAX_PACK_BYTES+1)
    return dict(content_sha256=sha,packed_sha256=digest(actual),raw_bytes=len(raw),packed_bytes=len(actual),read_only=True)


def read(sha):
    if not isinstance(sha,str) or not re.fullmatch('[0-9a-f]{64}',sha):raise ValueError('HUB_INVALID_ARTIFACT_ID')
    path=root()/'objects'/(sha+'.nhub')
    with path.open('rb') as source:packed=source.read(MAX_PACK_BYTES+1)
    raw=unpack(packed)
    if digest(raw)!=sha:raise ValueError('HUB_ARTIFACT_INTEGRITY')
    value=json.loads(raw)
    if not isinstance(value,dict):raise ValueError('HUB_INVALID_ARTIFACT')
    return value


def import_local(product,granularity=3600,limit=1000):
    if type(limit) is not int or not 2<=limit<=1000:raise ValueError('HUB_LOCAL_IMPORT_LIMIT')
    if product not in PRODUCTS or type(granularity) is not int or granularity not in GRANULARITIES:raise ValueError('HUB_MARKET_SCOPE')
    path=root().parent/'research_candles.sqlite3'
    if not path.exists():raise ValueError('COLLECT_LOCAL_CANDLES_FIRST')
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        rows=db.execute('SELECT payload,digest,revision_count FROM candles WHERE product=? AND granularity=? ORDER BY starts_at DESC LIMIT ?',(product,granularity,limit)).fetchall()
    if not rows:raise ValueError('COLLECT_LOCAL_CANDLES_FIRST')
    records=[]
    for raw,sha,revisions in reversed(rows):
        if revisions or digest(raw.encode())!=sha:raise ValueError('HUB_LOCAL_SOURCE_REVISED_OR_CORRUPT')
        records.append(json.loads(raw))
    return ingest(product,granularity,records)


def register_model(product,granularity,sha):
    if product not in PRODUCTS or type(granularity) is not int or granularity not in GRANULARITIES:raise ValueError('HUB_MARKET_SCOPE')
    model=read(sha)
    if model.get('schema')!='nexus.hub-momentum-model.v1' or model.get('product')!=product or model.get('granularity')!=granularity:raise ValueError('HUB_MODEL_IDENTITY')
    with closing(_db()) as db,db:
        db.execute('BEGIN IMMEDIATE')
        version=db.execute('SELECT version FROM generations WHERE product=? AND g=?',(product,granularity)).fetchone()
        if not version or version[0]!=model.get('dataset_generation'):raise ValueError('HUB_DATA_CHANGED_DURING_TRAINING')
        db.execute('CREATE TABLE IF NOT EXISTS model_refs(product TEXT,g INTEGER,sha TEXT NOT NULL,generation INTEGER NOT NULL,PRIMARY KEY(product,g))')
        db.execute('INSERT INTO model_refs VALUES(?,?,?,?) ON CONFLICT(product,g) DO UPDATE SET sha=excluded.sha,generation=excluded.generation',(product,granularity,sha,version[0]))


def latest_model(product,granularity):
    if product not in PRODUCTS or type(granularity) is not int or granularity not in GRANULARITIES:raise ValueError('HUB_MARKET_SCOPE')
    path=root()/'hub.sqlite3'
    if not path.exists():raise ValueError('HUB_NO_TRAINED_MODEL')
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='model_refs'").fetchone():raise ValueError('HUB_NO_TRAINED_MODEL')
        row=db.execute('SELECT sha,generation FROM model_refs WHERE product=? AND g=?',(product,granularity)).fetchone()
        version=db.execute('SELECT version FROM generations WHERE product=? AND g=?',(product,granularity)).fetchone()
        if row and (not version or row[1]!=version[0]):raise ValueError('HUB_MODEL_REQUIRES_REFRESH')
    if row is None:raise ValueError('HUB_NO_TRAINED_MODEL')
    return row[0]


def backfill(product,granularity=3600,now=None,transport=None):
    """Explicit bounded four-page public download, directly into canonical hub rows."""
    from nexus.historical_candles import fetch_candles
    from nexus.public_data import _opener
    from urllib.request import Request
    now=time.time() if now is None else now
    if product not in PRODUCTS or type(granularity) is not int or granularity not in GRANULARITIES:raise ValueError('HUB_MARKET_SCOPE')
    if type(now) not in (int,float) or not math.isfinite(now) or now<=600*granularity:raise ValueError('HUB_INVALID_BACKFILL_TIME')
    if transport is None:
        def transport(url):
            with _opener.open(Request(url,headers={'Accept':'application/json','User-Agent':'NexusOperative-HubResearch/1.0'}),timeout=5) as response:
                if response.status!=200 or response.geturl()!=url:raise ValueError('HUB_PUBLIC_RESPONSE_IDENTITY')
                raw=response.read(131073)
                if len(raw)>131072:raise ValueError('HUB_PUBLIC_RESPONSE_CAP')
                return json.loads(raw)
    end=int(now//granularity)*granularity;totals=dict(inserted=0,duplicates=0,quarantined=0)
    # Each page commits independently; successful earlier pages remain useful if
    # the next request fails. Retrying reuses canonical identities, not copies.
    for page in range(4):
        cutoff=end-page*150*granularity
        rows=fetch_candles(product,granularity,now=cutoff,transport=transport)
        rows=[r for r in rows if cutoff-150*granularity<=r['starts_at']<cutoff]
        if rows:
            result=ingest(product,granularity,rows,now=now)
            for key in totals:totals[key]+=result[key]
    return dict(**totals,pages_requested=4,source_verified=False,cloud_connected=False)


def generation(product,granularity):
    path=root()/'hub.sqlite3'
    if not path.exists():return 0
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        row=db.execute('SELECT version FROM generations WHERE product=? AND g=?',(product,granularity)).fetchone()
    return row[0] if row else 0
