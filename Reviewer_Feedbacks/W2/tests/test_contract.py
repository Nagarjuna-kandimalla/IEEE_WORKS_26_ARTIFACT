"""Scientific validity checks, independent of observed experimental outcomes."""
import sys
import unittest
from pathlib import Path
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from experiment import configure, ObservedHistory, ingest, snapshot, choose_audits, add_outcomes
import history
from summarize import matched_random

def rows(n=6):
    d=pd.DataFrame({c:np.ones(n) for c in history.SIGNATURE_COLUMNS})
    d['has_str_region']=False
    for c in ('workflow','process','version','system_config_id'): d[c]='same'
    d['logical_task_id']=[f't{i}' for i in range(n)]
    d['task_instance']=[f't{i}' for i in range(n)]
    d['split_group_id']=[f'g{i}' for i in range(n)]
    d['input_identity']=[f'in{i}' for i in range(n)]
    d['peak_memory_bytes']=np.arange(1,n+1)*100.
    d['ebpf_total_consumed_bytes']=np.arange(1,n+1)*1000.
    return d

class ContractTests(unittest.TestCase):
    def setUp(self): configure(True)
    def test_hidden_consumption_does_not_affect_history(self):
        d=rows(); scaler=history.SignatureScaler.fit(d)
        mask=np.array([True,False,True,False,True,False])
        a=ObservedHistory(48,512); ingest(a,d,scaler,mask)
        poisoned=d.copy(); poisoned.loc[~mask,'ebpf_total_consumed_bytes']=1e90
        b=ObservedHistory(48,512); ingest(b,poisoned,scaler,mask)
        pending=rows(1); pending.logical_task_id='pending'
        pd.testing.assert_frame_equal(snapshot(pending,a,scaler),snapshot(pending,b,scaler))
        self.assertEqual(snapshot(pending,a,scaler).history_i5_consumed_p50_bytes.iloc[0],3000.)
        self.assertEqual(a.peak,b.peak)
    def test_all_observed_matches_artifact_history(self):
        d=rows(); scaler=history.SignatureScaler.fit(d)
        a=ObservedHistory(48,512); b=history.HistoryIndex(48,512)
        ingest(a,d,scaler,np.ones(len(d),bool)); ingest(b,d,scaler,np.ones(len(d),bool))
        pd.testing.assert_frame_equal(snapshot(d,a,scaler,True),snapshot(d,b,scaler,True))
    def test_current_outcomes_cannot_change_snapshot(self):
        d=rows(); scaler=history.SignatureScaler.fit(d)
        state=ObservedHistory(48,512); ingest(state,d,scaler,np.ones(len(d),bool))
        a=snapshot(d,state,scaler)
        poisoned=d.copy(); poisoned['peak_memory_bytes']=1e80; poisoned['ebpf_total_consumed_bytes']=1e90
        pd.testing.assert_frame_equal(a,snapshot(poisoned,state,scaler))
    def test_no_audit_means_missing_not_zero(self):
        d=rows(); scaler=history.SignatureScaler.fit(d)
        state=ObservedHistory(48,512); ingest(state,d,scaler,np.zeros(len(d),bool))
        x=snapshot(d,state,scaler)
        self.assertTrue(x.history_i5_consumed_p50_bytes.isna().all())
        self.assertTrue(np.isfinite(x.history_i5_peak_p50_bytes).all())
        outcome=add_outcomes(x,d,np.zeros(len(d),bool))
        self.assertTrue(outcome.ebpf_total_consumed_bytes.isna().all())
    def test_equal_workflow_quotas_and_boundary_budgets(self):
        d=pd.DataFrame({'workflow':['a']*9+['b']*11,'process':['p']*20,'gate_risk_score':np.arange(20),'gate_information_score':np.arange(20)[::-1],'gate_discovery_score':np.arange(20)})
        for count in (0,1,4,10,20):
            a,_=choose_audits(d,count,'selective',1996,0,{'risk':.8,'information':.1,'discovery':.1})
            b,_=choose_audits(d,count,'random',1996,0,{'risk':.8,'information':.1,'discovery':.1})
            self.assertEqual(a.sum(),count); self.assertEqual(b.sum(),count)
            pd.testing.assert_series_equal(d.assign(selected=a).groupby('workflow').selected.sum(),d.assign(selected=b).groupby('workflow').selected.sum())
            if count in (0,20): np.testing.assert_array_equal(a,b)
    def test_matching_does_not_read_actual_peak(self):
        a=pd.DataFrame({'batch':[0,0,1,1],'request_mib':[10.,20.,12.,18.],'actual_peak_mib':[100.]*4})
        b=a.copy(); b.request_mib=[5.,10.,4.,6.]
        first=matched_random(a,b)
        a.actual_peak_mib=1.; b.actual_peak_mib=1e100
        second=matched_random(a,b)
        np.testing.assert_array_equal(first.matched_request_mib,second.matched_request_mib)
        np.testing.assert_allclose(first.groupby('batch').matched_request_mib.sum(),a.groupby('batch').request_mib.sum())

if __name__=='__main__': unittest.main()
