"""Paired selective/random audit experiment. All artifacts stay in this package.

Predictions and selections are frozen for a pending batch BEFORE its outcomes
are exposed. Only selected consumption labels enter history or training.
"""
from __future__ import annotations
import hashlib
import json
import math
import os
import sys
import time
import warnings
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'vendor'))
import joblib
import numpy as np
import pandas as pd
from sklearn.dummy import DummyClassifier, DummyRegressor
import common
import history
import modeling
import calibration
import selective_gates as gates

QUANTILES=[.5,.9,.95,.99,.995]
SCENARIO='a_plus_p_plus_c'
CURRENT_OUTCOMES={'peak_memory_bytes','ebpf_total_consumed_bytes','ebpf_read_return_bytes','ebpf_mmap_page_fault_bytes','runtime_seconds','observed_oom_flag'}

def configure(smoke=False):
    p=json.loads((ROOT/'protocol.json').read_text())
    config=json.loads((ROOT/'config/experiment.json').read_text())
    config['models'].update(n_estimators=8 if smoke else p['n_estimators'], folds=2 if smoke else p['crossfit_folds'],model_seeds=[20260728] if smoke else p['model_seeds'])
    # Immutable local configuration, shared by all vendored helpers in this process.
    for module in (common,history,modeling,calibration): module.load_config=lambda: config
    return p,config

def fingerprint(smoke):
    h=hashlib.sha256()
    paths=[ROOT/'protocol.json',ROOT/'config/experiment.json',ROOT/'requirements.txt']
    paths+=sorted((ROOT/'scripts').glob('*.py'))+sorted((ROOT/'vendor').glob('*.py'))
    paths+=[ROOT/('prepared_smoke' if smoke else 'prepared')/'manifest.json']
    for path in paths: h.update(path.read_bytes())
    h.update(str(smoke).encode())
    return h.hexdigest()

def atomic_dump(obj,path):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp')
    joblib.dump(obj,tmp,compress=3)
    os.replace(tmp,path)

class ObservedHistory(history.HistoryIndex):
    """Original scope definitions; consumption statistics ignore unaudited labels.

    Peak support/confidence/window/nearest-neighbour selection are unchanged.
    Consumption uses observed labels within that same selected scope window.
    Missing consumption stays missing, never zero.
    """
    def _nearest(self,context,vector,*,exclude_task_id=None):
        candidates=self.by_context.get(context,[])
        if not candidates: return [],np.asarray([],dtype=float)
        if not hasattr(self,'_nearest_cache'): self._nearest_cache={}
        entry=self._nearest_cache.get(context)
        if entry is None or entry[0]!=len(candidates):
            entry=(len(candidates),np.asarray(candidates),np.asarray([self.vectors[i] for i in candidates]),np.asarray([self.task_ids[i] for i in candidates]))
            self._nearest_cache[context]=entry
        ids,matrix,task_ids=entry[1:]
        keep=np.ones(len(ids),dtype=bool) if exclude_task_id is None else task_ids!=exclude_task_id
        ids,matrix=ids[keep],matrix[keep]
        distances=np.sqrt(np.mean((matrix-vector)**2,axis=1))
        count=min(self.nearest_k,len(ids))
        if not count: return [],np.asarray([],dtype=float)
        positions=np.argpartition(distances,count-1)[:count] if count<len(ids) else np.arange(len(ids))
        positions=positions[np.argsort(distances[positions])]
        return ids[positions].tolist(),distances[positions]

    def _stats(self,indices,*,distances=None):
        indices=list(indices)
        # Original peak statistics are unaffected by NaNs in the consumed array.
        result=super()._stats(indices,distances=distances)
        selected=indices[-self.scope_window:]
        consumed=np.asarray([self.consumed[i] for i in selected],dtype=float)
        static=np.asarray([self.static_input[i] for i in selected],dtype=float)
        valid=np.isfinite(consumed)
        values=np.quantile(consumed[valid],[.5,.95,.99]) if valid.any() else [np.nan]*3
        for suffix,value in zip(('p50','p95','p99'),values): result[f'consumed_{suffix}_bytes']=float(value)
        result['consumed_to_input_p50']=float(np.median(consumed[valid]/static[valid])) if valid.any() else np.nan
        return result

def legal_rows(rows):
    columns=set(history.SIGNATURE_COLUMNS)|set(modeling.CATEGORICAL_STATIC)|{'logical_task_id','task_instance','split_group_id','input_identity','decision_time','completion_time','batch','development_role','update_role'}
    # completion_time is bookkeeping only; it is not admitted by model/gate schemas.
    return rows[[c for c in rows if c in columns]].copy().reset_index(drop=True)

def snapshot(rows,state,scaler,exclude=False):
    clean=legal_rows(rows)
    vectors=scaler.transform(clean)
    values=[]
    for k,row in enumerate(clean.to_dict('records')):
        values.append(state.snapshot(row,vectors[k],exclude_task_id=str(row['logical_task_id']) if exclude else None))
    result=pd.concat([clean,pd.DataFrame(values)],axis=1)
    assert not CURRENT_OUTCOMES.intersection(result.columns)
    return result

def add_outcomes(features,rows,audited):
    result=features.copy().reset_index(drop=True)
    assert result.logical_task_id.tolist()==rows.logical_task_id.tolist()
    result['peak_memory_bytes']=rows.peak_memory_bytes.to_numpy(float)
    result['ebpf_total_consumed_bytes']=np.where(audited,rows.ebpf_total_consumed_bytes.to_numpy(float),np.nan)
    return result

def ingest(state,rows,scaler,audited):
    vectors=scaler.transform(rows)
    for i,row in enumerate(rows.to_dict('records')):
        row['ebpf_total_consumed_bytes']=float(row['ebpf_total_consumed_bytes']) if audited[i] else np.nan
        state.add(row,vectors[i])

class Ensemble:
    def __init__(self,transformer,members): self.transformer,self.members=transformer,members
    @classmethod
    def fit(cls,frame,target,view,quantiles,threads):
        transformer=modeling.preprocessor(view)
        x=transformer.fit_transform(modeling.materialize_model_features(frame))
        _,members=modeling._ensemble_fit_predict(x,np.log1p(np.maximum(np.asarray(target,float),0)),x[:1],quantiles=quantiles,model_seeds=common.load_config()['models']['model_seeds'],n_jobs=threads)
        return cls(transformer,members)
    def predict(self,frame):
        x=self.transformer.transform(modeling.materialize_model_features(frame))
        out={}
        for q,members in self.members.items():
            with warnings.catch_warnings():
                warnings.filterwarnings('ignore',message='X does not have valid feature names')
                out[q]=np.maximum(np.median(np.column_stack([np.expm1(m.predict(x)) for m in members]),axis=1),0)
        ordered=sorted(out)
        values=np.maximum.accumulate(np.column_stack([out[q] for q in ordered]),axis=1)
        return {q:values[:,i] for i,q in enumerate(ordered)}

class Predictor:
    @classmethod
    def fit(cls,train,threads):
        start=time.monotonic()
        obj=cls()
        train=train.copy().reset_index(drop=True)
        observed=np.isfinite(train.ebpf_total_consumed_bytes.to_numpy(float))
        audited=train.loc[observed].reset_index(drop=True)
        if len(audited)<10: raise ValueError('Insufficient observed consumption for training')
        # Current-row consumption never becomes a peak-model feature directly.
        cf,_=modeling.crossfit_predictions(modeling.materialize_model_features(audited),audited.ebpf_total_consumed_bytes.to_numpy(float),view='A+P',quantiles=[.5],n_jobs=threads)
        obj.consumption=Ensemble.fit(audited,audited.ebpf_total_consumed_bytes,'A+P',[.5],threads)
        chat=obj.consumption.predict(train)[.5]
        chat[observed]=cf[.5]
        train['c_hat_bytes']=chat
        target=train.peak_memory_bytes.to_numpy(float)/2**20
        obj.global_models=Ensemble.fit(train,target,'A+P+C',QUANTILES,threads)
        obj.process_models={}
        for key,group in train.groupby(['workflow','process'],sort=True):
            if len(group)<2: continue  # LightGBM requires at least two rows; use global fallback.
            obj.process_models[tuple(map(str,key))]=Ensemble.fit(group,group.peak_memory_bytes.to_numpy(float)/2**20,'A+P+C',QUANTILES[1:],threads)
        obj.training_ids=set(train.logical_task_id.astype(str))
        obj.info={'training_rows':len(train),'consumption_training_rows':len(audited),'process_models':len(obj.process_models),'seconds':time.monotonic()-start}
        return obj
    def predict(self,features):
        frame=features.copy().reset_index(drop=True)
        frame['c_hat_bytes']=self.consumption.predict(frame)[.5]
        global_values=self.global_models.predict(frame)
        values={q:v.copy() for q,v in global_values.items()}
        for key,group in frame.groupby(['workflow','process'],sort=True):
            model=self.process_models.get(tuple(map(str,key)))
            if model is not None:
                pred=model.predict(group)
                for q,v in pred.items(): values[q][group.index.to_numpy()]=v
        ordered=np.maximum.accumulate(np.column_stack([values[q] for q in QUANTILES]),axis=1)
        values={q:ordered[:,i] for i,q in enumerate(QUANTILES)}
        return frame,values,global_values

def base_values(values,policy):
    q={'point_q50':.5,'model_q90':.9,'model_q95':.95,'model_q99':.99,'model_q995':.995}[policy['base_policy']]
    return np.maximum(values[q],1e-6)

def residual_index(model,reference,scaler,policy):
    assert not model.training_ids.intersection(reference.logical_task_id.astype(str))
    _,values,_=model.predict(reference)
    return calibration.build_residual_index(reference,base_values(values,policy),scaler)

def allocate(model,features,index,scaler,policy):
    frame,values,global_values=model.predict(features)
    base=base_values(values,policy)
    vectors=scaler.transform(frame)
    corrections=[]
    scopes=[]
    for i,row in enumerate(frame.to_dict('records')):
        correction,scope=index.corrections(row,vectors[i],[policy['residual_quantile']],mode=policy['mode'])
        corrections.append(correction[policy['residual_quantile']])
        scopes.append(scope['calibration_scope'])
    request=np.ceil(np.maximum(base*np.exp(corrections),1))
    if not np.isfinite(request).all(): raise ValueError('Nonfinite allocation')
    for q in QUANTILES: frame[f'apc_q{int(round(q*1000)):03d}_prediction_mib']=values[q]
    frame['apc_global_q995_prediction_mib']=global_values[.995]
    frame['apc_active_request_mib']=request
    return frame,base,request,np.asarray(corrections),scopes

def fit_gates(frame,threads):
    """Artifact gate algorithms, with explicit fallbacks for degenerate labels."""
    targets=gates.add_gate_targets(frame,SCENARIO)
    features,numeric,categorical=gates.build_gate_features(targets,SCENARIO)
    transformer=gates.make_preprocessor(numeric,categorical)
    x=transformer.fit_transform(features)
    fitted={}
    degeneracies=[]
    for name,column in [('active_underallocation','active_underallocation'),('heavy_information','heavy_information_target')]:
        y=targets[column].astype(int).to_numpy()
        if len(np.unique(y))==2:
            model=gates._classifier(1996,threads)
            model.fit(x,y)
        else:
            model=ConstantProbability(float(y.mean()))
            degeneracies.append(name)
        fitted[name]=model
    positive=targets.active_underallocation.to_numpy(bool)
    if positive.sum()>=5:
        severity=gates._regressor(1996,threads)
        severity.fit(x[positive],np.log1p(targets.loc[positive,'active_shortfall_mib'].to_numpy(float)))
    else:
        severity=DummyRegressor(strategy='constant',constant=float(np.log1p(targets.loc[positive,'active_shortfall_mib']).mean()) if positive.any() else 0.)
        severity.fit(x[:1],np.zeros(1))
        degeneracies.append('conditional_shortfall')
    fitted['conditional_shortfall']=severity
    return {'scenario':SCENARIO,'preprocessor':transformer,'models':fitted,'feature_columns':tuple(features.columns),'degenerate_models':degeneracies,'risk_positives':int(positive.sum()),'gate_training_rows':len(frame)}

class ConstantProbability:
    def __init__(self,p): self.p=p
    def predict_proba(self,x): return np.tile([1-self.p,self.p],(x.shape[0],1))

def choose_audits(scored,count,method,seed,batch,lanes):
    rng=np.random.default_rng(np.random.SeedSequence([seed,batch,71 if method=='selective' else 83]))
    quotas=gates.proportional_quotas(scored.groupby('workflow',sort=True).size(),count,guarantee_one=True)
    if method=='selective':
        plan=gates.build_gate_plan(scored,count,lane_fractions=lanes)
        selected,_,lane=gates.select_from_plan(scored,plan,rng)
    else:
        selected=np.zeros(len(scored),bool)
        lane=np.full(len(scored),'not_selected',dtype=object)
        for w,group in scored.groupby('workflow',sort=True):
            ids=rng.choice(group.index.to_numpy(),quotas[w],replace=False)
            selected[ids]=True
            lane[ids]='random'
    assert selected.sum()==count
    observed=scored.assign(selected=selected).groupby('workflow').selected.sum().to_dict()
    assert observed==quotas
    return selected,lane

def initial_context(cohort,threads,smoke=False):
    p,config=configure(smoke)
    area=ROOT/('runs_smoke' if smoke else 'runs')
    checkpoint=area/cohort/'initial.joblib'
    tag=fingerprint(smoke)
    if checkpoint.exists():
        ctx=joblib.load(checkpoint)
        if ctx['fingerprint']!=tag: raise ValueError('Stale initial checkpoint; use a clean output directory')
        return ctx
    prepared=ROOT/('prepared_smoke' if smoke else 'prepared')/cohort
    initial=pd.read_pickle(prepared/'initial.pkl')
    fit=initial[initial.development_role.eq('fit')].reset_index(drop=True)
    ref=initial[initial.development_role.eq('calibrate')].reset_index(drop=True)
    gate=initial[initial.development_role.eq('gate')].reset_index(drop=True)
    scaler=history.SignatureScaler.fit(fit)
    state=ObservedHistory(config['history']['nearest_k'],config['history']['scope_window'])
    ingest(state,fit,scaler,np.ones(len(fit),bool))
    print(f'{cohort}: preparing development history ({len(initial)} rows)',flush=True)
    train=add_outcomes(snapshot(fit,state,scaler,exclude=True),fit,np.ones(len(fit),bool))
    reference=add_outcomes(snapshot(ref,state,scaler),ref,np.ones(len(ref),bool))
    gate_features=snapshot(gate,state,scaler)
    print(f'{cohort}: training initial predictor',flush=True)
    model=Predictor.fit(train,threads)
    policy=p['allocation'][cohort]
    residuals=residual_index(model,reference,scaler,policy)
    gate_frame,_,_,_,_=allocate(model,gate_features,residuals,scaler,policy)
    # The heavy-information target in the artifact needs an A+P point comparator.
    ap=Ensemble.fit(train,train.peak_memory_bytes.to_numpy(float)/2**20,'A+P',[.5],threads)
    gate_frame['ap_q500_prediction_mib']=ap.predict(gate_features)[.5]
    gate_frame['actual_peak_mib']=gate.peak_memory_bytes.to_numpy(float)/2**20
    gate_frame['actual_consumed_bytes']=gate.ebpf_total_consumed_bytes.to_numpy(float)
    gate_models=fit_gates(gate_frame,threads)
    # All initial data are known before evaluation begins, including held-out
    # development outcomes. Holdout labels never enter model fitting directly.
    state=ObservedHistory(config['history']['nearest_k'],config['history']['scope_window'])
    ingest(state,initial,scaler,np.ones(len(initial),bool))
    ctx={'fingerprint':tag,'model':model,'history':state,'scaler':scaler,'train':train,'reference':reference,'gates':gate_models,'policy':policy,'residuals':residuals,'next_batch':0,'timings':[]}
    atomic_dump(ctx,checkpoint)
    (checkpoint.parent/'initial_metadata.json').write_text(json.dumps({'model':model.info,'gates':{k:gate_models[k] for k in ('risk_positives','gate_training_rows','degenerate_models')},'fingerprint':tag},indent=2)+'\n')
    return ctx

def run_arm(cohort,budget,seed,method,threads,smoke=False):
    protocol,_=configure(smoke)
    area=ROOT/('runs_smoke' if smoke else 'runs')
    output=area/cohort/f'budget_{budget:.2f}'/f'seed_{seed}'/method
    output.mkdir(parents=True,exist_ok=True)
    tag=fingerprint(smoke)
    complete=output/'complete.json'
    if complete.exists():
        if json.loads(complete.read_text())['fingerprint']!=tag: raise ValueError('Stale completed output')
        return
    checkpoint=output/'state.joblib'
    if checkpoint.exists():
        ctx=joblib.load(checkpoint)
        if ctx['fingerprint']!=tag: raise ValueError('Stale arm checkpoint')
    else: ctx=initial_context(cohort,threads,smoke)
    prepared=ROOT/('prepared_smoke' if smoke else 'prepared')/cohort
    evaluation=pd.read_pickle(prepared/'evaluation.pkl')
    sizes=evaluation.groupby('batch',sort=True).size()
    total_count=int(math.floor(budget*len(evaluation)+.5))
    batch_counts=gates.proportional_quotas(sizes,total_count,guarantee_one=False)
    for batch in sorted(sizes.index):
        if batch<ctx['next_batch']: continue
        started=time.monotonic()
        rows=evaluation[evaluation.batch.eq(batch)].reset_index(drop=True)
        print(f'{cohort} {budget:.0%} {seed} {method}: batch {batch+1}, {len(rows)} tasks',flush=True)
        features=snapshot(rows,ctx['history'],ctx['scaler'])
        frame,base,request,correction,scopes=allocate(ctx['model'],features,ctx['residuals'],ctx['scaler'],ctx['policy'])
        # score_gate_models reads only legal model inputs; no batch labels exist
        # in frame. Selection is complete before add_outcomes or ingest.
        scored=gates.score_gate_models(ctx['gates'],frame)
        audited,lane=choose_audits(scored,batch_counts[batch],method,seed,int(batch),protocol['lane_fractions'])
        assert not CURRENT_OUTCOMES.intersection(frame.columns)
        predictions=rows[['logical_task_id','workflow','process','batch']].copy()
        predictions['request_mib']=request
        predictions['base_mib']=base
        predictions['q50_mib']=frame.apc_q500_prediction_mib
        predictions['c_hat_bytes']=frame.c_hat_bytes
        predictions['calibration_log_correction']=correction
        predictions['calibration_scope']=scopes
        predictions['audited']=audited
        predictions['audit_lane']=lane
        for c in ('gate_risk_score','gate_information_score','gate_discovery_score'): predictions[c]=scored[c]
        predictions['training_rows']=ctx['model'].info['training_rows']
        predictions['consumption_training_rows']=ctx['model'].info['consumption_training_rows']
        # Persist all decisions before consulting this batch's outcomes.
        predictions.to_csv(output/f'batch_{batch:02d}_decisions.tsv.gz',sep='\t',index=False)
        actual=rows.peak_memory_bytes.to_numpy(float)/2**20
        predictions['actual_peak_mib']=actual
        predictions['underallocated']=request<actual
        predictions['runtime_seconds']=rows.runtime_seconds.to_numpy(float)
        predictions.to_csv(output/f'batch_{batch:02d}_outcomes.tsv.gz',sep='\t',index=False)
        completed=add_outcomes(features,rows,audited)
        ingest(ctx['history'],rows,ctx['scaler'],audited)
        fit_mask=rows.update_role.eq('fit').to_numpy()
        ctx['train']=pd.concat([ctx['train'],completed.loc[fit_mask]],ignore_index=True)
        ctx['reference']=pd.concat([ctx['reference'],completed.loc[~fit_mask]],ignore_index=True)
        assert not set(ctx['train'].logical_task_id)&set(ctx['reference'].logical_task_id)
        ctx['next_batch']=int(batch)+1
        if batch!=max(sizes.index):
            ctx['model']=Predictor.fit(ctx['train'],threads)
            ctx['residuals']=residual_index(ctx['model'],ctx['reference'],ctx['scaler'],ctx['policy'])
        ctx['timings'].append({'batch':int(batch),'seconds':time.monotonic()-started,'audited':int(audited.sum()),'tasks':len(rows),'model':ctx['model'].info})
        atomic_dump(ctx,checkpoint)
        (output/'timings.json').write_text(json.dumps(ctx['timings'],indent=2)+'\n')
    complete.write_text(json.dumps({'fingerprint':tag,'cohort':cohort,'budget':budget,'seed':seed,'method':method,'tasks':len(evaluation),'audit_count':total_count,'smoke':smoke},indent=2)+'\n')

def run_pair(cohort,budget,seed,threads,smoke=False):
    for method in ('selective','random'): run_arm(cohort,budget,seed,method,threads,smoke)
