"""Bounded joint publication, historical-pin, and reclamation case.

Twenty updates over 24 sources / 144 facts, one reader, five compactions.
The expected dictionaries are advanced here without calling the transition,
reverse-index builder, or state-equality helpers. The retained current result
records successful completion under the guarded runner.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import queue
import tempfile
import threading
import time

from frontierstore.checker import run as independent_check
from frontierstore.model import Transaction, bootstrap_state
from frontierstore.store import FrontierStore

SOURCES, FACTS_PER_SOURCE, UPDATES, INTERVAL = 24, 6, 20, 4


def initial_case():
    sources = {f's{i:02d}': f'source {i}' for i in range(SOURCES)}
    specs = [
        {'id': f'f{i:02d}.{j}', 'payload': f'fact {i} {j}',
         'sources': sorted([f's{i:02d}', f's{(i+1)%SOURCES:02d}'])}
        for i in range(SOURCES) for j in range(FACTS_PER_SOURCE)
    ]
    expected = {
        'epoch': 1, 'schema': 1,
        'sources': {k: {'id': k, 'generation': 1, 'payload': v} for k, v in sources.items()},
        'facts': {x['id']: {'id': x['id'], 'payload': x['payload'], 'schema': 1,
                           'dependencies': [{'source': k, 'generation': 1} for k in x['sources']]}
                  for x in specs},
        'reverse': {k: sorted(x['id'] for x in specs if k in x['sources']) for k in sources},
    }
    return bootstrap_state(sources, specs), expected


def advance(expected, step):
    result = copy.deepcopy(expected)
    result['epoch'] += 1
    source = f's{(step*7)%SOURCES:02d}'
    result['sources'][source]['generation'] = result['epoch']
    result['sources'][source]['payload'] = f'change {step}'
    replacements = []
    # Independent complete scan, not the engine's reverse invalidation path.
    for key, fact in result['facts'].items():
        if any(edge['source'] == source for edge in fact['dependencies']):
            names = [edge['source'] for edge in fact['dependencies']]
            fact['payload'] = f'replacement {step} {key}'
            fact['dependencies'] = [{'source': k, 'generation': result['sources'][k]['generation']} for k in names]
            replacements.append({'id': key, 'payload': fact['payload'], 'sources': names})
    return result, Transaction(source_changes={source: f'change {step}'}, fact_replacements=replacements)


def reader_loop(store, commands, replies):
    held = None
    held_expected = None
    try:
        while True:
            action, expected = commands.get(timeout=10)
            if action == 'stop':
                replies.put(('ok', None)); return
            if action == 'hold':
                if held is not None:
                    raise AssertionError('double hold')
                held = store.snapshot()
                held_expected = copy.deepcopy(expected)
                if held.materialize() != held_expected:
                    raise AssertionError('held capture differs from endpoint')
            elif action == 'current':
                with store.snapshot() as current:
                    if current.materialize() != expected:
                        raise AssertionError('post-ack current acquisition differs from complete endpoint')
                if held is None or held.materialize() != held_expected:
                    raise AssertionError('historical snapshot changed or is missing')
            elif action == 'release':
                if held is None:
                    raise AssertionError('release without hold')
                held.close(); held = None; held_expected = None
            else:
                raise AssertionError('unknown action')
            replies.put(('ok', None))
    except BaseException as error:
        replies.put(('error', f'{type(error).__name__}: {error}'))
    finally:
        if held is not None:
            held.close()


def execute(output):
    if output.exists():
        raise FileExistsError('refusing to replace evidence')
    rows = []
    report = {'status': 'INCOMPLETE', 'sources': SOURCES, 'facts': SOURCES*FACTS_PER_SOURCE,
              'updates': UPDATES, 'reader_threads': 1, 'compaction_interval': INTERVAL,
              'oracle': 'complete endpoint dictionaries; older held handle compared to old endpoint',
              'rows': rows, 'comparison_scope': 'synthetic joint schedule, no new baseline timings'}
    try:
        with tempfile.TemporaryDirectory(prefix='frontier-joint-') as temporary:
            path = Path(temporary) / 'store'
            initial, expected = initial_case()
            store = FrontierStore.create(path, initial)
            if store.state.as_dict() != expected:
                raise AssertionError('bootstrap oracle mismatch')
            commands, replies = queue.Queue(), queue.Queue()
            reader = threading.Thread(target=reader_loop, args=(store, commands, replies), daemon=True)
            reader.start()

            def request(action, value=None):
                commands.put((action, copy.deepcopy(value)))
                status, detail = replies.get(timeout=5)
                if status != 'ok':
                    raise AssertionError(detail)

            protected = set(store.selected_segments)
            request('hold', expected)
            try:
                for step in range(1, UPDATES+1):
                    expected, transaction = advance(expected, step)
                    start = time.perf_counter_ns()
                    store.commit(transaction)
                    update_ns = time.perf_counter_ns()-start
                    # Queue handoff occurs after successful update return, not merely root fsync.
                    request('current', expected)
                    compact_ns = None
                    if step % INTERVAL == 0:
                        start = time.perf_counter_ns()
                        store.compact()
                        compact_ns = time.perf_counter_ns()-start
                        expected['epoch'] += 1
                        request('current', expected)
                    store.collect()
                    present = {p.name for p in (path/'segments').glob('segment-*.jsonl')}
                    if not protected <= present:
                        raise AssertionError('collector removed pinned history')
                    # No publisher or collector runs while the offline parser reads.
                    checked = independent_check(path)
                    if checked['status'] != 'ACCEPT':
                        raise AssertionError(checked)
                    row = {'update': step, 'epoch': expected['epoch'], 'update_ns': update_ns,
                           'compaction_ns': compact_ns, 'selected_segments': len(store.selected_segments),
                           'pinned_files': len(protected), 'current_endpoint_equal': True,
                           'historical_endpoint_equal': True, 'protected_present': True,
                           'quiescent_checker_accepted': True, 'released_history_collected': None}
                    if step % INTERVAL == 0:
                        request('release')
                        no_longer_needed = protected-set(store.selected_segments)
                        store.collect()
                        present = {p.name for p in (path/'segments').glob('segment-*.jsonl')}
                        if no_longer_needed & present:
                            raise AssertionError('released obsolete history not reclaimed')
                        row['released_history_collected'] = True
                        if step < UPDATES:
                            protected = set(store.selected_segments)
                            request('hold', expected)
                    rows.append(row)
                request('stop'); reader.join(timeout=5)
                if reader.is_alive():
                    raise AssertionError('reader failed to stop')
                if store.state.as_dict() != expected:
                    raise AssertionError('final endpoint mismatch')
                report['status'] = 'CASE_COMPLETED'
            finally:
                if reader.is_alive():
                    commands.put(('stop', None)); reader.join(timeout=5)
    except BaseException as error:
        report['error'] = f'{type(error).__name__}: {error}'
        raise
    finally:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2)+'\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = execute(args.output)
    print(json.dumps({'status': result['status'], 'completed_updates': len(result['rows'])}))
