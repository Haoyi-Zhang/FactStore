import argparse
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def render(path):
    data=json.loads((ROOT/'results/import-lifecycle/summary.json').read_text())
    if data['status']!='PASS' or not all(r['all_endpoints_equal'] for r in data['rows']):raise ValueError('incomplete native import lifecycle')
    if len({(r['incremental_source_reads'],r['full_source_reads'],r['incremental_source_bytes'],r['full_source_bytes']) for r in data['rows']})!=1:raise ValueError('unequal workload')
    row=data['rows'][0]
    fields=dict(ImportModules=data['modules'],ImportChanges=data['changed_modules'],ImportEngines=data['engines'],ImportTransitions=data['native_transitions'],
       ImportIncrementalReads=row['incremental_source_reads'],ImportFullReads=row['full_source_reads'],
       ImportIncrementalBytes=format(row['incremental_source_bytes'],','),ImportFullBytes=format(row['full_source_bytes'],','))
    path.write_text(''.join('\\newcommand{\\'+key+'}{'+str(value)+'}\n' for key,value in fields.items()),encoding='utf-8')
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);render(p.parse_args().output)
