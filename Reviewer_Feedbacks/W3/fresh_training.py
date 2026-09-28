"""Train both primary signal arms from frozen histories in a new directory."""
from pathlib import Path
import argparse
import os
import shutil
import subprocess
import sys

root=Path(__file__).resolve().parent
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('destination',type=Path)
p.add_argument('--threads',type=int,default=8)
a=p.parse_args();d=a.destination.resolve()
if d.exists():p.error('Destination must not already exist.')
d.mkdir(parents=True)
for name in ['scripts','pipeline','data','inputs']:
    shutil.copytree(root/name,d/name,ignore=shutil.ignore_patterns('__pycache__'))
shutil.copy2(root/'reproduce.py',d/'reproduce.py')
(d/'results').mkdir()
env=dict(os.environ,PYTHONHASHSEED='0',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',OMP_NUM_THREADS=str(a.threads),PYTHONDONTWRITEBYTECODE='1')
for mode in ['read_mmap','read']:
    subprocess.run([sys.executable,'scripts/run_arm.py','--mode',mode,'--seed','1996','--n-jobs',str(a.threads)],cwd=d,env=env,check=True)
subprocess.run([sys.executable,'reproduce.py'],cwd=d,env=env,check=True)
