"""Paired-block and per-case phase summaries of the retained native cohort.

No measurements are run or changed. Residuals are validation-harness work, not
estimates of hypothetical uninstrumented performance.
"""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
from experiments.paper_evidence_admission import admit_native
ROOT=Path(__file__).resolve().parents[1]

def summarize(directory=None):
    directory=Path(directory) if directory is not None else ROOT/'results/native-fanout'
    path=directory/'summary.json'
    data=admit_native(directory,ROOT);rows=data['rows']
    by={(r['dependency_width'],r['audit_every'],r['repetition'],r['engine']):r for r in rows}
    expected={(w,a,r,e) for w in (2,8) for a in (1,4) for r in range(3) for e in ('frontier','sqlite_n','sqlite_r')}
    if len(rows)!=36 or set(by)!=expected or not all(r['accepted'] for r in rows):
        raise ValueError('complete accepted paired native matrix required')
    pairs=[];phases=[]
    for w in (2,8):
        for a in (1,4):
            delta=[(by[w,a,r,'frontier']['lifecycle_wall_ns']-by[w,a,r,'sqlite_r']['lifecycle_wall_ns'])/1e6 for r in range(3)]
            pairs.append(dict(width=w,audit_every=a,differences_ms=delta,median_difference_ms=statistics.median(delta),
                frontier_wins=sum(d<0 for d in delta),sqlite_r_wins=sum(d>0 for d in delta),ties=sum(d==0 for d in delta)))
            for engine in ('frontier','sqlite_n','sqlite_r'):
                group=[by[w,a,r,engine] for r in range(3)]
                sums=[sum(r['stage_ns'].values())/1e6 for r in group]
                residuals=[r['lifecycle_wall_ns']/1e6-s for r,s in zip(group,sums)]
                if any(x<0 for x in residuals):raise ValueError('phase sum exceeds instrumented lifecycle')
                phases.append(dict(width=w,audit_every=a,engine=engine,
                    median_phase_sum_ms=statistics.median(sums),median_residual_ms=statistics.median(residuals),
                    median_wall_ms=statistics.median(r['lifecycle_wall_ns']/1e6 for r in group)))
    return dict(status='PASS',paired_blocks=12,measurements_rerun=0,pairs=pairs,phases=phases,
        input_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        scope='Three paired repetitions per configuration; residuals include checks/conversions/footprint and trace emission; no uninstrumented speedup estimated')

def tables(result):
    lines=[r'\begin{table}[t]\centering\small',r'\caption{Paired native lifecycle differences: FrontierStore minus SQLite-R (ms). Negative differences favor FrontierStore. The last column gives FrontierStore/SQLite-R win counts.}',
        r'\label{tab:native-paired}',r'\begin{tabular}{rrrrrrr}\toprule',r'Width & Audit & Rep. 0 & Rep. 1 & Rep. 2 & Median & Wins\\\midrule']
    for r in result['pairs']:
        lines.append(' & '.join([str(r['width']),str(r['audit_every']),*[f'{d:.2f}' for d in r['differences_ms']],f"{r['median_difference_ms']:.2f}",f"{r['frontier_wins']}/{r['sqlite_r_wins']}"])+r'\\')
    lines.extend([r'\bottomrule\end{tabular}\end{table}',r'\begin{table}[t]\centering\small',
        r'\caption{Native phase sums and residuals (ms). Each sum and residual is formed within a case before taking its median. Separate medians need not add to the wall-time median.}',
        r'\label{tab:native-residual}',r'\begin{tabular}{rrlrr}\toprule',r'Width & Audit & Engine & Phase sum & Residual\\\midrule'])
    names={'frontier':'FrontierStore','sqlite_n':'SQLite-N','sqlite_r':'SQLite-R'}
    for r in result['phases']:
        lines.append(f"{r['width']} & {r['audit_every']} & {names[r['engine']]} & {r['median_phase_sum_ms']:.2f} & {r['median_residual_ms']:.2f}"+r'\\')
    return '\n'.join(lines+[r'\bottomrule\end{tabular}\end{table}',''])

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--json',type=Path);parser.add_argument('--tex',type=Path)
    parser.add_argument('--check',type=Path);parser.add_argument('--check-tex',type=Path);args=parser.parse_args()
    result=summarize(args.check)
    if args.check_tex and args.check_tex.read_text(encoding='utf-8')!=tables(result):
        raise ValueError('paired native paper table differs from admitted evidence')
    for path,text in ((args.json,json.dumps(result,sort_keys=True,indent=2,allow_nan=False)+'\n'),(args.tex,tables(result))):
        if path:
            path.parent.mkdir(parents=True,exist_ok=True)
            with path.open('x',encoding='utf-8',newline='\n') as stream:stream.write(text)
    print(json.dumps(result,sort_keys=True,allow_nan=False))
