"""Bounded decoding and summary binding of the six retained lifecycle records."""
import gzip
import json
import math
from pathlib import Path

# Event records contain complete current/held endpoints, not just aggregate rows.
# The retained largest file is below this explicit per-file admission bound.
MAX_DECODED_BYTES = 32 * 1024 * 1024
CASES = {(size, policy) for size in (8, 64, 500) for policy in ('all', 'leases')}


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON member')
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError('nonfinite JSON constant: ' + value)


def finite_float(text):
    value = float(text)
    if not math.isfinite(value):
        raise ValueError('nonfinite JSON exponent')
    return value


def decode_record(path, *, limit=MAX_DECODED_BYTES):
    if type(limit) is not int or limit < 1:
        raise ValueError('positive decoded-byte limit required')
    with gzip.open(path, 'rb') as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('compressed record exceeds decoded-byte limit')
    value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_object,
                       parse_constant=reject_constant, parse_float=finite_float)
    if type(value) is not dict:
        raise ValueError('lifecycle record must be an object')
    return value


def bind_record(value, summary, size, policy):
    if (size, policy) not in CASES:
        raise ValueError('unknown lifecycle case')
    for key, expected in (('sources', size), ('facts', 6 * size), ('updates', 12),
                          ('raw_record_count', 15)):
        if type(value.get(key)) is not int or value[key] != expected:
            raise ValueError('lifecycle count mismatch: ' + key)
    if value.get('policy') != policy:
        raise ValueError('lifecycle policy mismatch')
    records = value.get('records')
    if type(records) is not list or len(records) != 15 or any(type(x) is not dict for x in records):
        raise ValueError('lifecycle event coverage mismatch')
    updates = [x for x in records if x.get('event') == 'update']
    if (len(updates) != 12 or any(type(x.get('index')) is not int for x in updates)
            or [x.get('index') for x in updates] != list(range(1, 13))):
        raise ValueError('lifecycle update sequence mismatch')
    if value.get('endpoint_equal') is not True or value.get('held_endpoint_equal') is not True:
        raise ValueError('lifecycle endpoint checks are not successful')
    reduced = {k: v for k, v in value.items() if k != 'records'}
    # Serialization distinguishes Boolean/integer values throughout the tree.
    encode = lambda x: json.dumps(x, sort_keys=True, allow_nan=False, separators=(',', ':'))
    if encode(reduced) != encode(summary):
        raise ValueError('compressed lifecycle record differs from summary')


def verify_compressed_records(root):
    root = Path(root)
    directory = root / 'results/review-lifecycle-20261014'
    with (directory / 'summary.json').open(encoding='utf-8') as handle:
        summary = json.load(handle, object_pairs_hook=unique_object,
                            parse_constant=reject_constant, parse_float=finite_float)
    rows = summary.get('rows')
    if type(rows) is not list or len(rows) != len(CASES):
        raise ValueError('six lifecycle summary rows required')
    index = {(row.get('sources'), row.get('policy')): row for row in rows if type(row) is dict}
    if len(index) != len(rows) or set(index) != CASES:
        raise ValueError('lifecycle summary case coverage mismatch')
    verified = set()
    for size, policy in sorted(CASES):
        path = directory / f'sources-{size}-{policy}.json.gz'
        bind_record(decode_record(path), index[size, policy], size, policy)
        verified.add(path.relative_to(root).as_posix())
    return verified
