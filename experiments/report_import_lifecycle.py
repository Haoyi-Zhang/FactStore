import argparse
import json
from pathlib import Path
from experiments.paper_evidence_admission import admit_import
ROOT=Path(__file__).resolve().parents[1]
def text(directory=None):
    data=admit_import(directory,ROOT)
    row=data['rows'][0]
    fields=dict(ImportModules=data['modules'],ImportChanges=data['changed_modules'],ImportEngines=data['engines'],ImportTransitions=data['native_transitions'],
       ImportIncrementalReads=row['incremental_source_reads'],ImportFullReads=row['full_source_reads'],
       ImportIncrementalBytes=format(row['incremental_source_bytes'],','),ImportFullBytes=format(row['full_source_bytes'],','))
    return ''.join('\\newcommand{\\'+key+'}{'+str(value)+'}\n' for key,value in fields.items())
def render(path,directory=None):
    content=text(directory)
    path.write_text(content,encoding='utf-8')
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path)
    p.add_argument('--check',type=Path);p.add_argument('--check-tex',type=Path);a=p.parse_args()
    content=text(a.check)
    if a.check_tex and a.check_tex.read_text(encoding='utf-8')!=content:raise ValueError('import paper macros differ')
    if a.output:a.output.write_text(content,encoding='utf-8')
    print(json.dumps(dict(status='RETAINED_IMPORT_INPUTS_CONSISTENT',new_campaigns=0)))
