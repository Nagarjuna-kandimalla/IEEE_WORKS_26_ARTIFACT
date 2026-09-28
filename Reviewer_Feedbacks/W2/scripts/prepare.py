"""Validate local inputs, freeze cohort membership, and assign pending batches."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]

def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

def stable(value):
    return hashlib.sha256(str(value).encode()).hexdigest()

def load_cohort(cohort):
    if cohort == 'rq2':
        rows = pd.read_csv(ROOT/'inputs/rq2/camp_ml_cohort_33516.csv.gz', low_memory=False)
        membership = pd.concat([pd.read_csv(p, sep='\t') for p in sorted((ROOT/'inputs/rq2').glob('*_1996.tsv'))])
        rows = rows.merge(membership[['logical_task_id','role','experiment_position']], on='logical_task_id', validate='one_to_one')
        initial = rows[rows.role.eq('train')].copy()
        evaluation = rows[rows.role.eq('test')].copy()
        assert (len(rows),len(initial),len(evaluation)) == (33516,28490,5026)
    else:
        initial = pd.read_csv(ROOT/'inputs/rq4/initial_causal_features.tsv.gz', sep='\t', low_memory=False)
        evaluation = pd.read_csv(ROOT/'inputs/rq4/test_causal_features.tsv.gz', sep='\t', low_memory=False)
        assert (len(initial),len(evaluation)) == (9141,21337)
    for d in (initial, evaluation):
        assert d.logical_task_id.is_unique
        for c in ('peak_memory_bytes','ebpf_total_consumed_bytes'):
            assert np.isfinite(d[c]).all() and d[c].ge(0).all(), c
        if {'ebpf_read_return_bytes','ebpf_mmap_page_fault_bytes'} <= set(d):
            assert np.allclose(d.ebpf_total_consumed_bytes, d.ebpf_read_return_bytes+d.ebpf_mmap_page_fault_bytes)
        d.drop(columns=[c for c in d if c.startswith('history_') or c.startswith('log1p_') or c == 'c_hat_bytes'], inplace=True)
    assert not set(initial.logical_task_id) & set(evaluation.logical_task_id)
    return initial.reset_index(drop=True), evaluation.reset_index(drop=True)

def assign(initial, evaluation, waves):
    initial = initial.copy()
    evaluation = evaluation.copy()
    # Group assignment avoids sharing a split_group_id across development roles.
    role_map = {}
    for workflow, frame in initial.groupby('workflow', sort=True):
        groups = frame.split_group_id.fillna('missing').astype(str).unique()
        groups = sorted(groups, key=lambda g: stable(f'development-v1::{workflow}::{g}'))
        n = len(groups)
        if n < 5:
            raise ValueError(f'{workflow}: too few development groups for fit/calibration/gate separation')
        fit_end, cal_end = int(n*.60), int(n*.80)
        for k,g in enumerate(groups):
            role_map[(workflow,g)] = 'fit' if k < fit_end else 'calibrate' if k < cal_end else 'gate'
    initial['development_role'] = [role_map[(w,str(g) if pd.notna(g) else 'missing')] for w,g in zip(initial.workflow,initial.split_group_id)]
    evaluation['batch'] = -1
    order = ['replay_position','decision_time','logical_task_id'] if 'replay_position' in evaluation else ['decision_time','logical_task_id']
    for _, group in evaluation.groupby(['workflow','process'], sort=True):
        group = group.sort_values(order,kind='stable')
        for batch, ids in enumerate(np.array_split(group.index.to_numpy(),waves)):
            evaluation.loc[ids,'batch'] = batch
    # Never train on reference-label rows: reserve 20% of each process/batch.
    evaluation['update_role'] = 'fit'
    for _,group in evaluation.groupby(['batch','workflow','process'],sort=True):
        ids = sorted(group.index,key=lambda i:stable('online-cal-v1::'+str(evaluation.loc[i,'logical_task_id'])))
        count = int(np.floor(len(ids)*.20))
        evaluation.loc[ids[:count],'update_role'] = 'calibrate'
    initial = initial.sort_values(['decision_time','workflow','process','logical_task_id'],kind='stable').reset_index(drop=True)
    evaluation = evaluation.sort_values(['batch']+order+['workflow','process'],kind='stable').reset_index(drop=True)
    return initial,evaluation

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--smoke',action='store_true')
    args=parser.parse_args()
    protocol=json.loads((ROOT/'protocol.json').read_text())
    output=ROOT/('prepared_smoke' if args.smoke else 'prepared')
    output.mkdir(exist_ok=True)
    source_files=list((ROOT/'inputs').rglob('*.gz'))+list((ROOT/'inputs').rglob('*.tsv'))+list((ROOT/'vendor').glob('*.py'))+[ROOT/'protocol.json',ROOT/'config/experiment.json']
    manifest={'status':'prepared_inputs_only','files':{str(p.relative_to(ROOT)):sha(p) for p in sorted(source_files)},'cohorts':{}}
    for cohort in protocol['cohorts']:
        initial,evaluation=load_cohort(cohort)
        if args.smoke:
            # Broad process coverage and enough groups for gate model smoke checks.
            initial=pd.concat([g.sample(min(60,len(g)),random_state=73129) for _,g in initial.groupby(['workflow','process'])],ignore_index=True)
            evaluation=pd.concat([g.sample(min(18,len(g)),random_state=73129) for _,g in evaluation.groupby(['workflow','process'])],ignore_index=True)
        initial,evaluation=assign(initial,evaluation,3 if args.smoke else protocol['pending_batches'])
        dest=output/cohort
        dest.mkdir(exist_ok=True)
        initial.to_pickle(dest/'initial.pkl')
        evaluation.to_pickle(dest/'evaluation.pkl')
        columns=['logical_task_id','workflow','process','split_group_id','decision_time','completion_time']
        initial[columns+['development_role']].to_csv(dest/'initial_assignment.tsv',sep='\t',index=False)
        evaluation[columns+['batch','update_role']].to_csv(dest/'evaluation_assignment.tsv',sep='\t',index=False)
        info={'initial':len(initial),'evaluation':len(evaluation),'workflows':int(initial.workflow.nunique()),'process_pairs':len(initial[['workflow','process']].drop_duplicates()),'development_roles':initial.development_role.value_counts().to_dict(),'batch_sizes':evaluation.groupby('batch').size().to_dict(),'missing_runtime':int(evaluation.runtime_seconds.isna().sum()),'assignment_hashes':{p.name:sha(p) for p in dest.glob('*.tsv')}}
        manifest['cohorts'][cohort]=info
        print(cohort,json.dumps(info),flush=True)
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')

if __name__=='__main__': main()
