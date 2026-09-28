"""Cohort-scoped entry point; original scientific scripts and fingerprints stay frozen."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import sys

for key in ('OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS','OMP_NUM_THREADS'):
    os.environ[key]='1'
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'scripts'))
import numpy as np
import pandas as pd
from experiment import fingerprint, initial_context
from run import worker
from prepare import load_cohort,assign,sha
from summarize import matched_random,metrics,bootstrap_mean_ci
import matplotlib.pyplot as plt

SCOPE=json.loads((ROOT/'workspace_scope.json').read_text())
COHORT=SCOPE['cohort']
PROTOCOL=json.loads((ROOT/'protocol.json').read_text())

def verify():
    manifest=json.loads((ROOT/'prepared/manifest.json').read_text())
    checked=0
    for name,expected in manifest['files'].items():
        if name.startswith('inputs/') and not name.startswith(f'inputs/{COHORT}/'):
            continue
        if sha(ROOT/name)!=expected: raise RuntimeError(f'Changed source/input: {name}')
        checked+=1
    if fingerprint(False)!=SCOPE['original_experiment_fingerprint']:
        raise RuntimeError('Original experiment fingerprint changed')
    for name,expected in manifest['cohorts'][COHORT]['assignment_hashes'].items():
        if sha(ROOT/'prepared'/COHORT/name)!=expected:
            raise RuntimeError(f'Changed assignment: {name}')
    print(f'Verified {COHORT}: {checked} input/source hashes, assignments and original code fingerprint.')

def prepare():
    initial,evaluation=load_cohort(COHORT)
    initial,evaluation=assign(initial,evaluation,PROTOCOL['pending_batches'])
    dest=ROOT/'prepared'/COHORT;dest.mkdir(parents=True,exist_ok=True)
    initial.to_pickle(dest/'initial.pkl');evaluation.to_pickle(dest/'evaluation.pkl')
    columns=['logical_task_id','workflow','process','split_group_id','decision_time','completion_time']
    initial[columns+['development_role']].to_csv(dest/'initial_assignment.tsv',sep='\t',index=False)
    evaluation[columns+['batch','update_role']].to_csv(dest/'evaluation_assignment.tsv',sep='\t',index=False)
    verify()

def summarize():
    verify()
    records=[]
    expected=[(b,s) for b in PROTOCOL['audit_budgets'] for s in PROTOCOL['sampling_seeds']]
    for budget,seed in expected:
        pair=ROOT/'runs'/COHORT/f'budget_{budget:.2f}'/f'seed_{seed}'
        tables=[]
        for method in ['selective','random']:
            meta=json.loads((pair/method/'complete.json').read_text())
            assert meta['fingerprint']==SCOPE['original_experiment_fingerprint']
            d=pd.concat([pd.read_csv(p,sep='\t') for p in sorted((pair/method).glob('batch_*_outcomes.tsv.gz'))],ignore_index=True)
            assert d.logical_task_id.is_unique and len(d)==meta['tasks']
            assert int(d.audited.sum())==meta['audit_count']
            tables.append(d)
        selective,random=tables
        assert selective.logical_task_id.tolist()==random.logical_task_id.tolist()
        assert np.array_equal(selective.actual_peak_mib,random.actual_peak_mib)
        assert selective.groupby(['batch','workflow']).audited.sum().equals(random.groupby(['batch','workflow']).audited.sum())
        first=selective.batch.eq(0)
        assert np.array_equal(selective.loc[first,'request_mib'],random.loc[first,'request_mib'])
        random=matched_random(selective,random)
        for evaluation in ['after_first_update','all_batches']:
            for method,frame in [('selective',selective),('random',random)]:
                rows=frame[frame.batch.gt(0)] if evaluation=='after_first_update' else frame
                for point in ['native','matched']:
                    column='matched_request_mib' if method=='random' and point=='matched' else 'request_mib'
                    records.append(dict(cohort=COHORT,budget=budget,seed=seed,method=method,evaluation=evaluation,operating_point=point,**metrics(rows,column)))
    results=pd.DataFrame(records)
    rng=np.random.default_rng(PROTOCOL['bootstrap_seed']);n=PROTOCOL['bootstrap_repetitions']
    summary=[]
    # Consume the same draws as the original two-cohort summary. This preserves
    # reported confidence intervals exactly without loading the other cohort.
    for cohort in sorted(PROTOCOL['cohorts']):
        for budget in sorted(PROTOCOL['audit_budgets']):
            for evaluation in ['after_first_update','all_batches']:
                for point in ['matched','native']:
                    if cohort!=COHORT:
                        bootstrap_mean_ci(np.zeros(len(PROTOCOL['sampling_seeds'])),rng,n);continue
                    g=results[(results.budget==budget)&(results.evaluation==evaluation)&(results.operating_point==point)]
                    paired=g.pivot(index='seed',columns='method',values='underallocation_rate')
                    mean,lo,hi=bootstrap_mean_ci(paired.selective-paired.random,rng,n)
                    summary.append(dict(cohort=cohort,budget=budget,evaluation=evaluation,operating_point=point,repetitions=len(paired),selective_minus_random_rate=mean,ci_low=lo,ci_high=hi,selective_rate=float(paired.selective.mean()),random_rate=float(paired.random.mean())))
    out=ROOT/'results/downstream_allocation';out.mkdir(parents=True,exist_ok=True)
    results.to_csv(out/'per_repetition_metrics.tsv',sep='\t',index=False)
    pd.DataFrame(summary).to_csv(out/'paired_summary.tsv',sep='\t',index=False)
    for point in ['native','matched']:
        fig,ax=plt.subplots(figsize=(5,3.8))
        for cohort in ['rq2','rq4']:
            for method,color,label in [('selective','#147d64','CAMP selective auditing'),('random','#bc6c25','Random sampling')]:
                xs=[];means=[];low=[];high=[]
                for budget in sorted(PROTOCOL['audit_budgets']):
                    if cohort!=COHORT:
                        bootstrap_mean_ci(np.zeros(len(PROTOCOL['sampling_seeds'])),rng,n);continue
                    g=results[(results.budget==budget)&results.evaluation.eq('after_first_update')&results.operating_point.eq(point)&results.method.eq(method)]
                    mean,lo,hi=bootstrap_mean_ci(g.underallocation_rate,rng,n)
                    xs.append(100*budget);means.append(100*mean);low.append(100*lo);high.append(100*hi)
                if cohort==COHORT:
                    ax.plot(xs,means,'o-',color=color,label=label);ax.fill_between(xs,low,high,color=color,alpha=.16)
        ax.set(title=SCOPE['label'],xlabel='Audit budget (%)',ylabel='Predicted OOM events after first update (%)')
        ax.grid(alpha=.2);ax.legend(fontsize=8)
        fig.suptitle('Matched total requested memory' if point=='matched' else 'Original memory requests')
        fig.tight_layout()
        for ext in ['png','pdf']:fig.savefig(out/f'auditing_underallocation_{point}.{ext}',dpi=220)
        plt.close(fig)
    (out/'status.json').write_text(json.dumps(dict(validation_only=False,partial=False,cohorts=[COHORT],completed_pairs=len(expected),missing_pairs=[],fingerprint=SCOPE['original_experiment_fingerprint'],scientific_status='Preliminary; extreme requests remain under investigation.',interval_scope='Bootstrap of five sampling-seed means on a fixed cohort; original bootstrap draw order preserved.',memory_matching='Per-batch analytical request-only scaling; fractional MiB in normalized comparison.'),indent=2)+'\n')
    print(f'Validated {len(expected)} paired runs and generated {COHORT}-only results. No model fitting performed.')

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['verify','prepare','run','summarize'])
    parser.add_argument('--workers',type=int,default=2)
    parser.add_argument('--threads',type=int,default=8)
    args=parser.parse_args()
    if args.action=='prepare':prepare()
    elif args.action=='verify':verify()
    elif args.action=='summarize':summarize()
    else:
        verify()
        if args.workers<1 or args.threads<1 or args.workers*args.threads>len(os.sched_getaffinity(0)):
            parser.error('workers * threads must fit available CPUs')
        pending=[]
        for b in PROTOCOL['audit_budgets']:
            for s in PROTOCOL['sampling_seeds']:
                pair=ROOT/'runs'/COHORT/f'budget_{b:.2f}'/f'seed_{s}'
                if not all((pair/m/'complete.json').exists() for m in ['selective','random']):pending.append((COHORT,b,s,args.threads,False))
        if not pending:
            print('All 25 paired runs already complete; no retraining requested or performed.');return
        initial_context(COHORT,args.threads,False)
        from concurrent.futures import ProcessPoolExecutor
        from multiprocessing import get_context
        with ProcessPoolExecutor(max_workers=args.workers,mp_context=get_context('spawn')) as pool:
            futures=[pool.submit(worker,*job) for job in pending]
            for f in futures:print(f.result())

if __name__=='__main__':main()
