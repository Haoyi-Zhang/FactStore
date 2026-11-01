"""Separate complete logical-export consumer. No SQLite or producer imports."""
import json
from pathlib import Path
import re


def check(condition, reason):
    if not condition:
        raise ValueError(reason)


def unique(pairs):
    output = {}
    for key, value in pairs:
        check(key not in output, 'duplicate JSON member')
        output[key] = value
    return output


def parse_bytes(data):
    check(type(data) is bytes and len(data) <= 32 * 1024 * 1024, 'bounded byte input')
    def nonfinite(value):
        raise ValueError('nonfinite JSON: ' + value)
    value = json.loads(data.decode('utf-8'), object_pairs_hook=unique, parse_constant=nonfinite)
    def shape(row, fields):
        check(type(row) is dict and set(row) == set(fields), 'exact field set')
    def ident(key):
        check(type(key) is str and re.fullmatch(r'[A-Za-z][A-Za-z0-9_.:-]{0,127}', key) is not None, 'identifier')
    def number(n, upper=(1 << 63) - 1):
        check(type(n) is int and 1 <= n <= upper, 'strict positive integer')
    def payload(s):
        check(type(s) is str, 'payload type')
        s.encode('utf-8', errors='strict')
    shape(value, ['epoch', 'schema', 'sources', 'facts', 'reverse'])
    number(value['epoch'], 99_999_999); number(value['schema'])
    for name in ['sources', 'facts', 'reverse']:
        check(type(value[name]) is dict, 'map shape')
    inverse = {key: set() for key in value['sources']}
    for key, source in value['sources'].items():
        ident(key); shape(source, ['id', 'generation', 'payload'])
        check(source['id'] == key, 'source key mismatch')
        number(source['generation'], value['epoch']); payload(source['payload'])
    for key, fact in value['facts'].items():
        ident(key); shape(fact, ['id', 'payload', 'schema', 'dependencies'])
        check(fact['id'] == key, 'fact key mismatch'); payload(fact['payload'])
        number(fact['schema']); check(fact['schema'] == value['schema'], 'schema mismatch')
        check(type(fact['dependencies']) is list and fact['dependencies'], 'nonempty dependency list')
        order = []
        for edge in fact['dependencies']:
            shape(edge, ['source', 'generation']); ident(edge['source']); number(edge['generation'])
            sid = edge['source']; order.append(sid)
            check(sid in value['sources'], 'absent dependency source')
            check(edge['generation'] == value['sources'][sid]['generation'], 'stale generation')
            inverse[sid].add(key)
        check(order == sorted(set(order)), 'duplicate/unsorted dependency')
    check(set(value['reverse']) == set(inverse), 'reverse domain')
    for sid, members in value['reverse'].items():
        check(type(members) is list, 'reverse list')
        for member in members:
            ident(member)
        check(members == sorted(inverse[sid]), 'exact reverse membership')
    return value


def load(path):
    with Path(path).open('rb') as stream:
        data = stream.read(32 * 1024 * 1024 + 1)
    return parse_bytes(data)


def require_endpoint(actual, expected):
    check(actual == expected, 'complete endpoint mismatch, not merely closure')
