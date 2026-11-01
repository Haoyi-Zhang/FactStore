"""Owned input/spec generator; imports no producer, SQL or export consumer."""
import copy


def inverse(state):
    result = {sid: [] for sid in state['sources']}
    for fid in sorted(state['facts']):
        for edge in state['facts'][fid]['dependencies']:
            result[edge['source']].append(fid)
    return result


def specified_transition(previous, tx):
    # Deliberately scans old fact dependency lists; SQL lowering uses indexed
    # dependency queries. This function only generates the frozen expected input.
    state = copy.deepcopy(previous)
    state['epoch'] += 1
    schema = state['schema'] if tx['new_schema'] is None else tx['new_schema']
    changed = set(tx['source_changes'])
    retracted = set(tx['fact_retractions'])
    for fid, fact in list(state['facts'].items()):
        if schema != state['schema'] or fid in retracted or any(e['source'] in changed for e in fact['dependencies']):
            del state['facts'][fid]
    state['schema'] = schema
    for sid, payload in tx['source_changes'].items():
        if payload is None:
            state['sources'].pop(sid, None)
        else:
            state['sources'][sid] = dict(id=sid, generation=state['epoch'], payload=payload)
    for row in tx['fact_replacements']:
        state['facts'][row['id']] = dict(id=row['id'], payload=row['payload'], schema=schema,
            dependencies=[dict(source=sid, generation=state['sources'][sid]['generation']) for sid in sorted(row['sources'])])
    state['reverse'] = inverse(state)
    return state


def history(size, width):
    sources = {f's{i:04d}': dict(id=f's{i:04d}', generation=1,
        payload=['', 'nul\0payload', 'λ分析', 'quote"\\text'][i % 4] + str(i)) for i in range(size)}
    facts = {}
    for i in range(size * 6):
        fid = f'f{i:05d}'
        ids = sorted(f's{(i + 7*j) % size:04d}' for j in range(width))
        facts[fid] = dict(id=fid, payload=f'fact:{i}:Ω\0', schema=1,
            dependencies=[dict(source=sid, generation=1) for sid in ids])
    state = dict(epoch=1, schema=1, sources=sources, facts=facts)
    state['reverse'] = inverse(state)
    initial = copy.deepcopy(state)
    events = []
    for step in range(1, 9):
        tx = dict(source_changes={}, fact_replacements=[], fact_retractions=[], new_schema=None)
        if step == 1:
            tx['source_changes']['s0000'] = 'edited:λ\0'
            for fid, fact in state['facts'].items():
                if any(e['source'] == 's0000' for e in fact['dependencies']):
                    tx['fact_replacements'].append(dict(id=fid, payload=fact['payload'] + ':refresh', sources=[e['source'] for e in fact['dependencies']]))
        elif step == 2:
            tx['fact_replacements'] = [dict(id='f00000', payload='fact-only:new', sources=['s0002', 's0003'])]
        elif step == 3:
            tx['source_changes'] = {'sExtra': 'inserted'}
            tx['fact_replacements'] = [dict(id='fExtra', payload='new:fact', sources=['s0002', 'sExtra'])]
        elif step == 5:
            tx['source_changes'] = {'sExtra': None}
            tx['fact_retractions'] = ['f00001']
        elif step == 6:
            tx['new_schema'] = 2
            tx['fact_replacements'] = [dict(id=fid, payload=fact['payload'] + ':schema2', sources=[e['source'] for e in fact['dependencies']]) for fid, fact in state['facts'].items()]
        elif step == 7:
            tx['source_changes'] = {f's{size-1:04d}': 'last-source:updated'}
        elif step == 8:
            tx['fact_replacements'] = [dict(id='fFinal', payload='final\0λ', sources=['s0000', f's{size-1:04d}'])]
        state = specified_transition(state, tx)
        events.append(dict(step=step, transaction=copy.deepcopy(tx), expected=copy.deepcopy(state)))
    return dict(id=f'n{size}-w{width}', sources=size, width=width, initial=initial, events=events)


def fixed_inputs():
    return dict(version='owned-sql-export-input-v1', histories=[history(n, w) for n in (16, 64, 256) for w in (2, 8)],
        held_future_schedule=[dict(repetition=r, scale=n, width=w,
            order=['sql_logical_export', 'frontier'] if (r + (w == 8)) % 2 == 0 else ['frontier', 'sql_logical_export'])
            for r in range(7) for n in (16, 64, 256) for w in (2, 8)],
        future_schedule_status='HOLD, NOT EXECUTED; duplicate inputs are not independent programs')
