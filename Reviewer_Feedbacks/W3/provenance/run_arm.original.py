#!/usr/bin/env python3
"""Fit one signal arm using the artifact's unaltered modeling/calibration routines."""
from pathlib import Path
import argparse
import json
import os
import sys
import time
import socket
import hashlib
import io

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipeline'))
import joblib
import numpy as np
import pandas as pd
from common import load_config, load_experiment_rows, training_sort, experiment_sort, MIB, write_json
from history import SignatureScaler, materialize_causal_history
from modeling import (materialize_model_features, crossfit_predictions,
    fit_final_predictions, crossfit_per_process_predictions,
    fit_final_per_process_predictions, configured_model_seeds, feature_columns)
from calibration import select_allocation_policy, apply_policy_sequentially
from run_offline_experiment import validate_population, prediction_frame


def progress(message):
    print(time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), message, flush=True)


def artifact_tsv_roundtrip(frame):
    """Match the artifact's disk handoff between global and process-tail stages."""
    return pd.read_csv(io.StringIO(frame.to_csv(sep='\t',index=False)),sep='\t',low_memory=False)


def prepare(mode):
    destination = ROOT / 'data' / mode
    destination.mkdir(exist_ok=True)
    if (destination / 'READY.json').exists():
        return destination
    rows = load_experiment_rows(1996, split_seed=1996)
    source = pd.read_csv('/work/IEEE_WORKS_26_ARTIFACT/DATA/RQ2/camp_ml_cohort_33516.csv.gz')
    pd.testing.assert_frame_equal(
        source.sort_values('logical_task_id').reset_index(drop=True),
        rows[source.columns].sort_values('logical_task_id').reset_index(drop=True),
        check_dtype=False, check_exact=True)
    rows['ablation_consumed_bytes'] = rows['ebpf_read_return_bytes']
    if mode == 'read_mmap':
        rows['ablation_consumed_bytes'] += rows['ebpf_mmap_page_fault_bytes']
    assert (rows['ablation_consumed_bytes'] >= 0).all()
    initial = training_sort(rows.loc[rows.role == 'train']).reset_index(drop=True)
    test = experiment_sort(rows.loc[rows.role == 'test']).reset_index(drop=True)
    validate_population(rows, initial, test)
    assert initial.logical_task_id.is_unique and test.logical_task_id.is_unique
    assert set(initial.logical_task_id).isdisjoint(test.logical_task_id)
    scaler = SignatureScaler.fit(initial)
    progress(f'{mode}: materializing consumption-specific history')
    initial, test = materialize_causal_history(initial, test, scaler)
    initial.to_pickle(destination / 'initial.pkl')
    test.to_pickle(destination / 'test.pkl')
    joblib.dump(scaler, destination / 'scaler.joblib')
    write_json(destination / 'READY.json', {'mode':mode,'train':len(initial),'test':len(test)})
    return destination


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['read','read_mmap'], required=True)
    parser.add_argument('--seed', type=int, default=1996)
    parser.add_argument('--n-jobs',type=int,default=8)
    parser.add_argument('--prepare-only',action='store_true')
    args=parser.parse_args()
    os.environ['CAMP_SIGNAL_MODE']=args.mode
    os.environ['CAMP_REPLICATE_SEED']=str(args.seed)
    destination=prepare(args.mode)
    if args.prepare_only:return
    output=ROOT/'results'/args.mode/f'seed_{args.seed}'
    models=ROOT/'models'/args.mode/f'seed_{args.seed}'
    output.mkdir(parents=True,exist_ok=True)
    models.mkdir(parents=True,exist_ok=True)
    if (output/'COMPLETE.json').exists():
        progress(f'{args.mode}/{args.seed}: already complete');return
    start=time.time()
    initial=pd.read_pickle(destination/'initial.pkl')
    test=pd.read_pickle(destination/'test.pkl')
    scaler=joblib.load(destination/'scaler.joblib')
    initial=materialize_model_features(initial)
    test=materialize_model_features(test)
    consumed=initial['ablation_consumed_bytes'].to_numpy(float)
    target=initial['peak_memory_bytes'].to_numpy(float)/MIB
    quantiles=[0.5,0.9,0.95,0.99,0.995]
    tails=quantiles[1:]
    metadata={'signal':args.mode,'seed':args.seed,'split_seed':1996,
        'n_jobs':args.n_jobs,'host':socket.gethostname(),'model_seeds':configured_model_seeds(),
        'train_rows':len(initial),'test_rows':len(test),
        'history_contract':'artifact warm leave-one-out development; sequential test',
        'omitted_work':'A+P comparator and unused consumed-to-RSS diagnostic fits; neither feeds APC predictions'}

    progress(f'{args.mode}/{args.seed}: fit consumption proxy')
    cache=output/'proxy.npz'
    if cache.exists():
        z=np.load(cache); c_oof={0.5:z['oof']}; c_test={0.5:z['test']}
    else:
        c_oof,mo=crossfit_predictions(initial,consumed,view='A+P',quantiles=[0.5],n_jobs=args.n_jobs)
        c_test,mt=fit_final_predictions(initial,test,consumed,view='A+P',quantiles=[0.5],
            n_jobs=args.n_jobs,model_root=models/'proxy')
        write_json(output/'proxy_metadata.json',{'oof':mo,'final':mt})
        np.savez_compressed(cache,oof=c_oof[0.5],test=c_test[0.5])
    # The retained worker control fits its global memory stage from the saved
    # history/proxy TSVs. Match that handoff symmetrically in both signal arms.
    initial=artifact_tsv_roundtrip(pd.read_pickle(destination/'initial.pkl'))
    test=artifact_tsv_roundtrip(pd.read_pickle(destination/'test.pkl'))
    initial['c_hat_bytes']=artifact_tsv_roundtrip(pd.DataFrame({'c_hat':c_oof[0.5]})).c_hat.to_numpy()
    test['c_hat_bytes']=artifact_tsv_roundtrip(pd.DataFrame({'c_hat':c_test[0.5]})).c_hat.to_numpy()
    initial=materialize_model_features(initial);test=materialize_model_features(test)
    used=set(sum((list(x) for x in feature_columns('A+P+C')),[]))
    assert not used.intersection({'ablation_consumed_bytes','ebpf_read_return_bytes',
        'ebpf_mmap_page_fault_bytes','ebpf_total_consumed_bytes','peak_memory_bytes'})

    progress(f'{args.mode}/{args.seed}: fit global memory ensembles')
    cache=output/'global.npz'
    if cache.exists():
        z=np.load(cache)
        global_oof={q:z[f'oof_{q}'] for q in quantiles}
        global_test={q:z[f'test_{q}'] for q in quantiles}
    else:
        global_oof,mo=crossfit_predictions(initial,target,view='A+P+C',quantiles=quantiles,n_jobs=args.n_jobs)
        global_test,mt=fit_final_predictions(initial,test,target,view='A+P+C',quantiles=quantiles,
            n_jobs=args.n_jobs,model_root=models/'global')
        write_json(output/'global_metadata.json',{'oof':mo,'final':mt})
        np.savez_compressed(cache,**{f'oof_{q}':v for q,v in global_oof.items()},
            **{f'test_{q}':v for q,v in global_test.items()})

    # train_process_tail.py reloads feature/proxy/prediction TSVs. Preserve this
    # float-parsing boundary: tiny rounding differences can affect tree splits.
    initial=artifact_tsv_roundtrip(pd.read_pickle(destination/'initial.pkl'))
    test=artifact_tsv_roundtrip(pd.read_pickle(destination/'test.pkl'))
    initial['c_hat_bytes']=artifact_tsv_roundtrip(pd.DataFrame({'c_hat':c_oof[0.5]})).c_hat.to_numpy()
    test['c_hat_bytes']=artifact_tsv_roundtrip(pd.DataFrame({'c_hat':c_test[0.5]})).c_hat.to_numpy()
    initial=materialize_model_features(initial);test=materialize_model_features(test)
    target=initial['peak_memory_bytes'].to_numpy(float)/MIB
    global_oof_frame=artifact_tsv_roundtrip(pd.DataFrame({str(q):v for q,v in global_oof.items()}))
    global_test_frame=artifact_tsv_roundtrip(pd.DataFrame({str(q):v for q,v in global_test.items()}))
    global_oof={q:global_oof_frame[str(q)].to_numpy() for q in quantiles}
    global_test={q:global_test_frame[str(q)].to_numpy() for q in quantiles}
    progress(f'{args.mode}/{args.seed}: fit workflow-process tail ensembles')
    cache=output/'local_oof.npz'
    if cache.exists():
        z=np.load(cache);local_oof={q:z[str(q)] for q in tails}
    else:
        local_oof,mo=crossfit_per_process_predictions(initial,target,view='A+P+C',quantiles=tails,
            n_jobs=args.n_jobs,fallback_predictions=global_oof)
        write_json(output/'local_oof_metadata.json',mo)
        np.savez_compressed(cache,**{str(q):v for q,v in local_oof.items()})
    cache=output/'local_test.npz'
    if cache.exists():
        z=np.load(cache);local_test={q:z[str(q)] for q in tails}
    else:
        local_test,mt=fit_final_per_process_predictions(initial,test,target,view='A+P+C',quantiles=tails,
            n_jobs=args.n_jobs,model_root=models/'local',fallback_predictions=global_test)
        write_json(output/'local_test_metadata.json',mt)
        np.savez_compressed(cache,**{str(q):v for q,v in local_test.items()})
    oof={0.5:global_oof[0.5],**local_oof}
    pred={0.5:global_test[0.5],**local_test}
    for values in [oof,pred]:
        ordered=np.maximum.accumulate(np.column_stack([values[q] for q in quantiles]),axis=1)
        for j,q in enumerate(quantiles):values[q]=ordered[:,j]

    progress(f'{args.mode}/{args.seed}: select development policy and replay allocation')
    policy,candidates=select_allocation_policy(initial,oof,scaler,mode='hierarchical')
    allocation=apply_policy_sequentially(initial,test,oof,pred,scaler,policy,update_during_test=True)
    tasks=prediction_frame(variant='A+P+C',seed=args.seed,test=test,predictions=pred,
        allocation=allocation,c_hat=test['c_hat_bytes'].to_numpy(),policy=policy)
    tasks['signal_mode']=args.mode
    tasks['split_group_id']=test['split_group_id'].to_numpy()
    tasks.to_csv(output/'task_predictions.tsv.gz',sep='\t',index=False)
    dev=initial[['logical_task_id','workflow','process','split_group_id']].copy()
    dev['actual_peak_mib']=target
    for q,v in oof.items():dev[f'q{int(q*1000):03d}_oof_mib']=v
    dev.to_csv(output/'initial_oof_predictions.tsv.gz',sep='\t',index=False)
    candidates.to_csv(output/'calibration_candidates.csv',index=False)
    write_json(output/'selected_policy.json',policy)
    metadata.update(elapsed_seconds=time.time()-start,underallocations=int(tasks.initial_oom.sum()),
        request_gib=float(tasks.first_allocation_mib.sum()/1024),
        mdape_pct=float(100*np.median(abs(tasks.point_q50_prediction_mib/tasks.actual_peak_mib-1))),
        prediction_sha256=hashlib.sha256((output/'task_predictions.tsv.gz').read_bytes()).hexdigest())
    write_json(output/'COMPLETE.json',metadata)
    progress(json.dumps(metadata))


if __name__=='__main__':main()
