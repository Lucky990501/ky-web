"""Test-only receipt V2. Exact PRIMARY column/type contract, not a DB writer."""
import datetime as dt
import hashlib
import json
import uuid

VERSION = 'RECEIPT_ROW_CANONICALIZATION_V2'
# PostgreSQL ordinal order, including every field. IDs are TEXT, not PG UUID.
COLUMNS = {
    'tenants': ('id', 'name', 'poc_api_key', 'created_at'),
    'enterprise_configs': ('tenant_id', 'payload', 'updated_at'),
    'users': ('id', 'tenant_id', 'email', 'password_hash', 'display_name', 'role',
              'created_at', 'avatar_storage_key', 'avatar_mime_type', 'account_status'),
}
TIMES = {'tenants': 'created_at', 'enterprise_configs': 'updated_at', 'users': 'created_at'}
NULLABLE = {'poc_api_key', 'avatar_storage_key', 'avatar_mime_type'}

def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('utf-8')

def json_value(value):
    if value is None or type(value) in (str, bool, int): return value
    if type(value) is float:
        # No NaN/Infinity; same numeric JSON semantics for Python and PG JSON.
        return json.loads(encode(value))
    if isinstance(value, uuid.UUID): return str(value)
    if isinstance(value, list): return [json_value(x) for x in value]
    if isinstance(value, dict) and all(type(k) is str for k in value):
        return {k: json_value(value[k]) for k in sorted(value)}
    raise ValueError('UNSUPPORTED_CANONICAL_VALUE')

def timestamptz(value):
    if type(value) is str: value = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if not isinstance(value, dt.datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('NAIVE_TIMESTAMPTZ_REJECTED')
    return value.astimezone(dt.timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')

def row(table, value, version=VERSION):
    if version != VERSION: raise ValueError('RECEIPT_VERSION_REJECTED')
    if table not in COLUMNS or set(value) != set(COLUMNS[table]): raise ValueError('ROW_FIELD_SET_REJECTED')
    pairs=[]
    for name in COLUMNS[table]:
        v=value[name]
        if v is None:
            if name not in NULLABLE: raise ValueError('NON_NULL_COLUMN_REJECTED')
        elif name == TIMES[table]: v=timestamptz(v)
        elif table == 'enterprise_configs' and name == 'payload':
            if type(v) is str: v=json.loads(v)
            if type(v) is not dict: raise ValueError('CONFIG_JSON_OBJECT_REQUIRED')
            v=json_value(v)
        elif type(v) is not str: raise ValueError('TEXT_COLUMN_REJECTED')
        pairs.append([name,v])
    return {'version': version, 'table': table, 'columns': pairs}

def row_bytes(table, value, version=VERSION): return encode(row(table,value,version))
def row_sha256(table, value, version=VERSION): return hashlib.sha256(row_bytes(table,value,version)).hexdigest()
def rows_sha256(table, values, version=VERSION):
    # Full encoded row ordering retains duplicates, unlike set-based hashing.
    rows=sorted(row_bytes(table,x,version) for x in values)
    return hashlib.sha256(encode({'version':version,'table':table,'rows':[json.loads(x) for x in rows]})).hexdigest()

def verify_catalog(fields):
    expected=[]
    for table, names in COLUMNS.items():
        for i,name in enumerate(names,1):
            expected.append((table,name,i,'timestamptz' if name==TIMES[table] else 'text',
                             6 if name==TIMES[table] else None,'YES' if name in NULLABLE else 'NO'))
    actual=[(x['table_name'],x['column_name'],x['ordinal_position'],x['udt_name'],x['datetime_precision'],x['is_nullable']) for x in fields]
    if sorted(actual) != sorted(expected): raise ValueError('CANONICAL_PG_TYPE_CONTRACT_DRIFT')
