#!/usr/bin/env python3
"""Regenerate only the exact RQ2 causal features and C-hat predictions."""
import json, os, time
from pathlib import Path
import joblib
import pandas as pd
from common import ROOT, load_config, load_experiment_rows, experiment_sort, training_sort, write_json
from history import SignatureScaler, materialize_causal_history
from modeling import crossfit_predictions, fit_final_predictions, materialize_model_features
from run_offline_experiment import validate_population

seed=1996
os.environ['CAMP_REPLICATE_SEED']=str(seed)
started=time.time(); config=load_config()
rows=load_experiment_rows(seed)
initial=training_sort(rows.loc[rows.role=='train']).reset_index(drop=True)
test=experiment_sort(rows.loc[rows.role=='test']).reset_index(drop=True)
validate_population(rows,initial,test)
out=ROOT/'results'/f'seed_{seed}'; models=ROOT/'models'/f'seed_{seed}'
out.mkdir(parents=True,exist_ok=True); models.mkdir(parents=True,exist_ok=True)
scaler=SignatureScaler.fit(initial); joblib.dump(scaler,models/'signature_scaler.joblib')
initial_h,test_h=materialize_causal_history(initial,test,scaler)
initial_h.to_csv(out/'initial_causal_features.tsv',sep='\t',index=False)
test_h.to_csv(out/'test_causal_features.tsv',sep='\t',index=False)
initial_f=materialize_model_features(initial_h); test_f=materialize_model_features(test_h)
target=pd.to_numeric(initial_f[config['consumed_target']],errors='raise').to_numpy(float)
oof,oof_meta=crossfit_predictions(initial_f,target,view='A+P',quantiles=[0.5],n_jobs=8)
pred,final_meta=fit_final_predictions(initial_f,test_f,target,view='A+P',quantiles=[0.5],n_jobs=8,model_root=models/'consumed_c_hat')
pd.DataFrame({'logical_task_id':initial_f.logical_task_id,'role':'train_oof','c_hat_bytes':oof[0.5],'actual_consumed_bytes':target}).to_csv(out/'c_hat_initial_oof.tsv',sep='\t',index=False)
pd.DataFrame({'logical_task_id':test_f.logical_task_id,'role':'test','c_hat_bytes':pred[0.5],'actual_consumed_bytes':pd.to_numeric(test_f[config['consumed_target']],errors='raise')}).to_csv(out/'c_hat_test.tsv',sep='\t',index=False)
write_json(out/'run_manifest.json',{'experiment':'exact_c_hat_ablation','seed':seed,'initial_rows':len(initial),'test_rows':len(test),'variants':[],'variant_metadata':{},'c_hat_oof':oof_meta,'c_hat_final':final_meta,'elapsed_seconds':time.time()-started})
print(json.dumps({'initial_rows':len(initial),'test_rows':len(test),'elapsed_seconds':time.time()-started},indent=2))
