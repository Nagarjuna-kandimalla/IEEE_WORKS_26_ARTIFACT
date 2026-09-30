#!/usr/bin/env python3
import argparse, json, os, time
from pathlib import Path
import joblib, numpy as np, pandas as pd
from calibration import apply_policy_sequentially, select_allocation_policy
from common import MIB, ROOT, load_config, write_json
from modeling import crossfit_predictions, fit_final_predictions, materialize_model_features, feature_columns
from run_offline_experiment import prediction_frame

paths={'A+P':'a_plus_p','A+P+CH':'a_plus_p_plus_ch','A+P+CHAT':'a_plus_p_plus_chat','A+P+C':'a_plus_p_plus_c'}
p=argparse.ArgumentParser(); p.add_argument('--variant',choices=paths,required=True); p.add_argument('--n-jobs',type=int,default=8); args=p.parse_args()
os.environ['CAMP_REPLICATE_SEED']='1996'; config=load_config(); out=ROOT/'results/seed_1996'; models=ROOT/'models/seed_1996'
initial=pd.read_csv(out/'initial_causal_features.tsv',sep='\t',low_memory=False); test=pd.read_csv(out/'test_causal_features.tsv',sep='\t',low_memory=False)
ci=pd.read_csv(out/'c_hat_initial_oof.tsv',sep='\t'); ct=pd.read_csv(out/'c_hat_test.tsv',sep='\t')
if not np.array_equal(initial.logical_task_id.astype(str),ci.logical_task_id.astype(str)) or not np.array_equal(test.logical_task_id.astype(str),ct.logical_task_id.astype(str)): raise ValueError('C-hat alignment')
initial['c_hat_bytes']=ci.c_hat_bytes.to_numpy(float); test['c_hat_bytes']=ct.c_hat_bytes.to_numpy(float)
initial=materialize_model_features(initial); test=materialize_model_features(test)
target=pd.to_numeric(initial[config['rss_target']],errors='raise').to_numpy(float)/MIB
qs=[float(x) for x in config['models']['quantiles']]; variant=args.variant; d=paths[variant]; root=out/d; root.mkdir(parents=True,exist_ok=True); started=time.time()
oof,oof_meta=crossfit_predictions(initial,target,view=variant,quantiles=qs,n_jobs=args.n_jobs)
pred,final_meta=fit_final_predictions(initial,test,target,view=variant,quantiles=qs,n_jobs=args.n_jobs,model_root=models/d)
scaler=joblib.load(models/'signature_scaler.joblib'); policy,candidates=select_allocation_policy(initial,oof,scaler,mode='hierarchical')
allocation=apply_policy_sequentially(initial,test,oof,pred,scaler,policy,update_during_test=True)
tasks=prediction_frame(variant=variant,seed=1996,test=test,predictions=pred,allocation=allocation,c_hat=ct.c_hat_bytes.to_numpy(float),policy=policy)
if variant in {'A+P+CHAT','A+P+C'}: tasks['c_hat_bytes']=ct.c_hat_bytes.to_numpy(float)
tasks.to_csv(root/'task_predictions.tsv',sep='\t',index=False)
of=pd.DataFrame({'logical_task_id':initial.logical_task_id,'workflow':initial.workflow,'process':initial.process,'actual_peak_mib':target})
for q,v in oof.items(): of[f'q{int(round(q*1000)):03d}_oof_mib']=v
of.to_csv(root/'initial_oof_predictions.tsv',sep='\t',index=False); candidates.to_csv(root/'calibration_candidates.csv',index=False); write_json(root/'selected_policy.json',policy)
write_json(root/'global_metadata.json',{'variant':variant,'oof':oof_meta,'final':final_meta,'policy':policy,'numeric_features':list(feature_columns(variant)[0]),'categorical_features':list(feature_columns(variant)[1]),'elapsed_seconds':time.time()-started})
print(json.dumps({'variant':variant,'elapsed_seconds':time.time()-started,'underallocations':int(tasks.initial_oom.sum()),'request_gib':float(tasks.first_allocation_mib.sum()/1024)},indent=2))
