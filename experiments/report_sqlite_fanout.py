"""Unmeasured, fail-closed paper receiver. Never call the frozen timing driver."""
import argparse
import json
from pathlib import Path

from experiments.paper_evidence_admission import admit_api


def table(result):
    # Presentation-only copy of the frozen producer's table format.
    lines=[r'\begin{table}[t]\centering\small',
        r'\caption{Current-source SQLite-API sensitivity; median of three lifecycles.}',
        r'\label{tab:fanout-cadence}',r'\begin{tabular}{rrrrrr}\toprule',
        r'Width & Audit interval & Replaced/update & Transition (ms) & Audit (ms) & Full (ms)\\\midrule']
    for row in result['aggregates']:
        stage=row['median_stage_ms']
        lines.append(f"{row['dependency_width']} & {row['audit_every']} & {row['mean_replaced_facts']:.1f} & {stage['transition']:.2f} & {stage['audit']:.2f} & {row['median_lifecycle_ms']:.2f}"+r'\\')
    lines.extend([r'\bottomrule\end{tabular}\end{table}'])
    return '\n'.join(lines)+'\n'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check',type=Path)
    parser.add_argument('--check-tex',type=Path)
    parser.add_argument('--tex',type=Path)
    args=parser.parse_args()
    result=admit_api(args.check)
    content=table(result)
    if args.check_tex and args.check_tex.read_text(encoding='utf-8')!=content:
        raise ValueError('API paper table differs from admitted raw evidence')
    if args.tex:args.tex.write_text(content,encoding='utf-8')
    print(json.dumps(dict(status='RETAINED_API_INPUTS_CONSISTENT',cases=12,
                          current_updates=96,new_campaigns=0),allow_nan=False))


if __name__=='__main__':main()
