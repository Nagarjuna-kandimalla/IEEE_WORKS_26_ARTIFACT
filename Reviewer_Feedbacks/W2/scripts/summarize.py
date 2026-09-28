"""Verify pairing and produce native/matched-memory tables and figures."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from experiment import ROOT, fingerprint

def matched_random(selective,random):
    # Predetermined aggregate normalization. No outcome enters the factor.
    result=random.copy()
    result['matched_request_mib']=result.request_mib.astype(float)
    for batch,rows in selective.groupby('batch'):
        mask=result.batch.eq(batch)
        factor=float(rows.request_mib.sum()/result.loc[mask,'request_mib'].sum())
        result.loc[mask,'matched_request_mib']=result.loc[mask,'request_mib']*factor
        result.loc[mask,'matching_factor']=factor
        assert np.isclose(result.loc[mask,'matched_request_mib'].sum(),rows.request_mib.sum(),rtol=1e-12)
    return result

def metrics(frame,request_col):
    req=frame[request_col].to_numpy(float)
    peak=frame.actual_peak_mib.to_numpy(float)
    valid=np.isfinite(frame.runtime_seconds.to_numpy(float)) & frame.runtime_seconds.gt(0).to_numpy()
    return {'tasks':len(frame),'underallocations':int((req<peak).sum()),'underallocation_rate':float((req<peak).mean()),'coverage':float((req>=peak).mean()),'requested_gib':float(req.sum()/1024),'shortfall_gib':float(np.maximum(peak-req,0).sum()/1024),'positive_unused_gib':float(np.maximum(req-peak,0).sum()/1024),'positive_unused_gib_hours_known_runtime':float((np.maximum(req[valid]-peak[valid],0)*frame.runtime_seconds.to_numpy(float)[valid]).sum()/1024/3600),'runtime_metric_tasks':int(valid.sum())}

def bootstrap_mean_ci(values,rng,n):
    values=np.asarray(values,float)
    means=np.mean(rng.choice(values,size=(n,len(values)),replace=True),axis=1)
    return float(values.mean()),*map(float,np.quantile(means,[.025,.975]))

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--smoke',action='store_true')
    parser.add_argument('--allow-partial',action='store_true')
    args=parser.parse_args()
    protocol=json.loads((ROOT/'protocol.json').read_text())
    area=ROOT/('runs_smoke' if args.smoke else 'runs')
    output=ROOT/('validation' if args.smoke else 'results/downstream_allocation')
    output.mkdir(parents=True,exist_ok=True)
    tag=fingerprint(args.smoke)
    records=[]
    complete_jobs=[]
    for selective_done in sorted(area.glob('*/budget_*/seed_*/selective/complete.json')):
        pair=selective_done.parent.parent
        random_done=pair/'random/complete.json'
        if not random_done.exists(): continue
        meta=json.loads(selective_done.read_text())
        for path in (selective_done,random_done): assert json.loads(path.read_text())['fingerprint']==tag
        tables=[]
        for method in ('selective','random'):
            d=pd.concat([pd.read_csv(p,sep='\t') for p in sorted((pair/method).glob('batch_*_outcomes.tsv.gz'))],ignore_index=True)
            assert d.logical_task_id.is_unique and len(d)==meta['tasks']
            assert int(d.audited.sum())==meta['audit_count']
            tables.append(d)
        selective,random=tables
        assert selective.logical_task_id.tolist()==random.logical_task_id.tolist()
        assert np.array_equal(selective.actual_peak_mib,random.actual_peak_mib)
        assert selective.groupby(['batch','workflow']).audited.sum().equals(random.groupby(['batch','workflow']).audited.sum())
        first=selective.batch.eq(0)
        assert np.array_equal(selective.loc[first,'request_mib'],random.loc[first,'request_mib']), 'Starting predictors differ'
        random=matched_random(selective,random)
        random[['logical_task_id','batch','matching_factor','matched_request_mib']].to_csv(pair/'memory_matching.tsv.gz',sep='\t',index=False)
        for scope in ('after_first_update','all_batches'):
            for method,table in [('selective',selective),('random',random)]:
                table=table[table.batch.gt(0)] if scope=='after_first_update' else table
                for operating_point in ('native','matched'):
                    column='matched_request_mib' if method=='random' and operating_point=='matched' else 'request_mib'
                    records.append({'cohort':meta['cohort'],'budget':meta['budget'],'seed':meta['seed'],'method':method,'evaluation':scope,'operating_point':operating_point,**metrics(table,column)})
        complete_jobs.append((meta['cohort'],meta['budget'],meta['seed']))
    if not records: raise RuntimeError('No completed paired jobs')
    expected={(c,b,s) for c in protocol['cohorts'] for b in protocol['audit_budgets'] for s in protocol['sampling_seeds']}
    missing=sorted(expected-set(complete_jobs))
    if not args.smoke and missing and not args.allow_partial:
        raise RuntimeError(f'{len(missing)} paired jobs missing; refuse final summary. Use --allow-partial for an explicitly preliminary summary.')
    results=pd.DataFrame(records)
    results.to_csv(output/'per_repetition_metrics.tsv',sep='\t',index=False)
    rng=np.random.default_rng(protocol['bootstrap_seed'])
    summary=[]
    groupcols=['cohort','budget','evaluation','operating_point']
    for keys,g in results.groupby(groupcols,sort=True):
        paired=g.pivot(index='seed',columns='method',values='underallocation_rate')
        delta=paired['selective']-paired['random']
        mean,lo,hi=bootstrap_mean_ci(delta,rng,protocol['bootstrap_repetitions'])
        summary.append(dict(zip(groupcols,keys))|{'repetitions':len(paired),'selective_minus_random_rate':mean,'ci_low':lo,'ci_high':hi,'selective_rate':float(paired.selective.mean()),'random_rate':float(paired.random.mean())})
    pd.DataFrame(summary).to_csv(output/'paired_summary.tsv',sep='\t',index=False)
    for operating_point in ('native','matched'):
        fig,axes=plt.subplots(1,2,figsize=(9,3.5),sharey=True)
        for ax,cohort,title in zip(axes,['rq2','rq4'],['CAMP 85/15 split','Sizey-comparison 30/70 split']):
            part=results[(results.cohort==cohort)&results.evaluation.eq('after_first_update')&results.operating_point.eq(operating_point)]
            for method,color,label in [('selective','#147d64','CAMP selective auditing'),('random','#bc6c25','Random sampling')]:
                xs=[]; ys=[]; lows=[]; highs=[]
                for budget,g in part[part.method.eq(method)].groupby('budget',sort=True):
                    mean,lo,hi=bootstrap_mean_ci(g.underallocation_rate,rng,protocol['bootstrap_repetitions'])
                    xs.append(100*budget); ys.append(100*mean); lows.append(100*lo); highs.append(100*hi)
                ax.plot(xs,ys,'o-',color=color,label=label)
                ax.fill_between(xs,lows,highs,color=color,alpha=.16)
            ax.set_title(title); ax.set_xlabel('Audit budget (%)'); ax.grid(alpha=.2)
        axes[0].set_ylabel('Underallocation after first update (%)')
        axes[1].legend(fontsize=8)
        status='VALIDATION ONLY — ' if args.smoke else ('PRELIMINARY — ' if missing else '')
        fig.suptitle(status+('Matched total requested memory' if operating_point=='matched' else 'Native memory requests'))
        fig.tight_layout()
        fig.savefig(output/f'auditing_underallocation_{operating_point}.png',dpi=220)
        fig.savefig(output/f'auditing_underallocation_{operating_point}.pdf')
        plt.close(fig)
    (output/'status.json').write_text(json.dumps({'validation_only':args.smoke,'partial':bool(missing),'completed_pairs':len(complete_jobs),'missing_pairs':missing,'fingerprint':tag,'interval_scope':'Bootstrap of sampling-seed means on fixed cohorts; not uncertainty over new datasets/workflows. Five repetitions give limited precision.','memory_matching':'Per-batch analytical scaling uses requests only; fractional MiB allowed in normalized comparison. Native requests retain integer MiB.'},indent=2)+'\n')
    print(f'Validated {len(complete_jobs)} paired jobs; wrote {output}',flush=True)

if __name__=='__main__': main()
