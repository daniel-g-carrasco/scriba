"""Public AMI naming experiment, using saved model tensors and ground truth.

Named tile spans are synthetic, derived from AMI annotations. No real Teams
capture, names or recordings enter this test. Outputs refuse overwriting.
"""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))


def main():
    import numpy as np
    from scriba.core import naming
    from scriba.core.segments import Segment
    p=argparse.ArgumentParser()
    p.add_argument('--intermediates',type=Path,required=True)
    p.add_argument('--rttm',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    args=p.parse_args()
    if args.out.exists(): raise FileExistsError(args.out)
    turns=[]
    for row in args.rttm.read_text().splitlines():
        fields=row.split()
        turns.append((float(fields[3]),float(fields[3])+float(fields[4]),fields[7]))
    with np.load(args.intermediates,allow_pickle=False) as arrays:
        vectors=arrays['embeddings']; binary=arrays['segmentation']
    grouped={}
    for i,window in enumerate(vectors):
        # A window's centre must lie in one annotated voice only. The local
        # voice is selected by the central model mask, not by a numeric label.
        at=i+5
        active=[who for start,end,who in turns if start<=at<end]
        if len(active)!=1: continue
        local=int(binary[i,296].argmax())
        if binary[i,296].sum()!=1 or not np.isfinite(window[local]).all(): continue
        grouped.setdefault(active[0],[]).append(window[local])
    people=sorted(who for who,values in grouped.items() if len(values)>=20)
    if len(people)<2: raise ValueError('Insufficient clean annotated AMI voices')
    a,b=people[:2]
    centroid=lambda values: np.mean(values,axis=0).tolist()
    ca,cb=centroid(grouped[a]),centroid(grouped[b])
    lines=[Segment(start,end,'public AMI speech',who) for start,end,who in turns if who in (a,b)]
    spans=[('Example tile',start,end) for start,end,who in turns if who in (a,b)]
    different,notes=naming.propose_with_voices(lines,spans,{a:ca,b:cb})
    split_vectors=grouped[a]
    c1,c2=centroid(split_vectors[::2]),centroid(split_vectors[1::2])
    own=[t for t in turns if t[2]==a]
    split=[Segment(start,end,'public AMI speech','split-'+str(i%2)) for i,(start,end,_) in enumerate(own)]
    merged,merge_notes=naming.propose_with_voices(split,[('Example tile',s,e) for s,e,_ in own],
                                               {'split-0':c1,'split-1':c2})
    report=dict(different_distance=naming.cosine_distance(ca,cb),
        split_distance=naming.cosine_distance(c1,c2),samples={a:len(grouped[a]),b:len(grouped[b])},
        different={k:v.name for k,v in different.items()},different_notes=notes,
        split={k:v.name for k,v in merged.items()},split_notes=merge_notes,
        thresholds=dict(similar=naming.SIMILAR_DISTANCE,different=naming.DIFFERENT_DISTANCE),
        passed='multiple_voices' in notes and 'merge_voices' in merge_notes,
        limitation='Synthetic tile spans and annotation-selected model embeddings, not real Teams ground truth')
    with args.out.open('x',encoding='utf-8') as out: json.dump(report,out,indent=2)
    print(json.dumps(report))
    return 0 if report['passed'] else 1

if __name__=='__main__': sys.exit(main())
