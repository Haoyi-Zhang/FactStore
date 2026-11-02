"""Default build preflight: compare all four saved table/macro inputs, no writes.

This is strict saved-byte admission and arithmetic, not native endpoint replay.
The original complete native replay remains in native_fanout_results.verify_dataset.
"""
import argparse
import json
from pathlib import Path
import statistics

from experiments.paper_evidence_admission import ROOT, NATIVE_PHASES, ENGINES, admit_api, admit_native
from experiments.report_import_lifecycle import text as import_text
from experiments.report_native_pairs import summarize, tables
from experiments.report_sqlite_fanout import table as api_table
from experiments.native_fanout_results import render_table as native_table


def native_medians(data):
    medians=[]
    for width in (2,8):
        for cadence in (1,4):
            for engine in ENGINES:
                group=[r for r in data['rows'] if (r['dependency_width'],r['audit_every'],r['engine'])==
                       (width,cadence,engine)]
                medians.append(dict(width=width,audit_interval=cadence,engine=engine,repetitions=3,
                    median_wall_ms=round(statistics.median(r['lifecycle_wall_ns']/1e6 for r in group),3),
                    median_phase_ms={k:round(statistics.median(r['stage_ns'][k]/1e6 for r in group),3)
                                     for k in NATIVE_PHASES}))
    return dict(medians=medians)


def check(paper):
    contents={
        'fanout-cadence.tex':api_table(admit_api()),
        'native-fanout.tex':native_table(native_medians(admit_native())),
        'native-paired.tex':tables(summarize()),
        'import-lifecycle-results.tex':import_text(),
    }
    for name,content in contents.items():
        if (paper/name).read_text(encoding='utf-8')!=content:
            raise ValueError('paper input differs from admitted saved evidence: '+name)
    return dict(status='PAPER_INPUTS_IDENTICAL',files=sorted(contents),new_campaigns=0,
                scope='strict saved bytes, full matrices and retained-data arithmetic; no native replay')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--paper-dir',type=Path,default=ROOT.parent/'paper')
    print(json.dumps(check(p.parse_args().paper_dir),sort_keys=True))
