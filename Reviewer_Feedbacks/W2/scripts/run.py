"""Launch resumable paired auditing experiments on a single multicore host."""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from multiprocessing import get_context
import json
import os
from pathlib import Path
import time

# Limit BLAS pools BEFORE importing NumPy/LightGBM. LightGBM receives n_jobs.
for variable in ('OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS','OMP_NUM_THREADS'):
    os.environ[variable]='1'

from experiment import ROOT, configure, initial_context, run_pair
from prepare import sha

def worker(cohort,budget,seed,threads,smoke):
    from contextlib import redirect_stdout,redirect_stderr
    log=ROOT/('runs_smoke' if smoke else 'runs')/cohort/f'budget_{budget:.2f}'/f'seed_{seed}'/'run.log'
    log.parent.mkdir(parents=True,exist_ok=True)
    with log.open('a',buffering=1) as handle,redirect_stdout(handle),redirect_stderr(handle):
        run_pair(cohort,budget,seed,threads,smoke)
    return str(log.relative_to(ROOT))

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--smoke',action='store_true')
    p.add_argument('--cohorts',nargs='+',choices=['rq2','rq4'])
    p.add_argument('--budgets',nargs='+',type=float)
    p.add_argument('--seeds',nargs='+',type=int)
    p.add_argument('--threads',type=int,default=8)
    p.add_argument('--workers',type=int,default=2)
    p.add_argument('--initial-only',action='store_true')
    args=p.parse_args()
    if args.workers<1 or args.threads<1: p.error('workers and threads must be positive')
    if args.workers*args.threads>len(os.sched_getaffinity(0)):
        p.error('workers * threads exceeds available logical CPUs')
    protocol,_=configure(args.smoke)
    cohorts=args.cohorts or protocol['cohorts']
    budgets=args.budgets or ([.2] if args.smoke else protocol['audit_budgets'])
    seeds=args.seeds or ([1996] if args.smoke else protocol['sampling_seeds'])
    if not args.smoke and (not set(budgets)<=set(protocol['audit_budgets']) or not set(seeds)<=set(protocol['sampling_seeds'])):
        p.error('Production budget/seed must be in the frozen protocol')
    if any(b<0 or b>1 for b in budgets): p.error('budget outside [0,1]')
    manifest_path=ROOT/('prepared_smoke' if args.smoke else 'prepared')/'manifest.json'
    manifest=json.loads(manifest_path.read_text())
    for name,expected in manifest['files'].items():
        if sha(ROOT/name)!=expected: raise ValueError(f'Input/config hash mismatch: {name}; rerun prepare explicitly')
    # Preparation is shared, completed serially before workers can read it.
    for cohort in cohorts: initial_context(cohort,args.threads,args.smoke)
    if args.initial_only: return
    started=time.monotonic()
    jobs=[(c,b,s,args.threads,args.smoke) for c in cohorts for b in budgets for s in seeds]
    if args.workers==1:
        for job in jobs: print('Finished',worker(*job),flush=True)
    else:
        # Fresh processes avoid inheriting initialized LightGBM/OpenMP pools.
        with ProcessPoolExecutor(max_workers=args.workers,mp_context=get_context('spawn')) as pool:
            futures=[pool.submit(worker,*job) for job in jobs]
            for future in as_completed(futures): print('Finished',future.result(),flush=True)
    print(f'Completed {len(jobs)} paired jobs in {time.monotonic()-started:.1f} seconds',flush=True)

if __name__=='__main__': main()
