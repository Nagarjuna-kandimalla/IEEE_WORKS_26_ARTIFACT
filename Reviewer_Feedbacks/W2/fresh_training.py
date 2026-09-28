"""Run the full original W2 training experiment in a new output directory."""
from pathlib import Path
import argparse
import shutil
import subprocess
import sys

root=Path(__file__).resolve().parent
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('destination',type=Path)
p.add_argument('--workers',type=int,default=2)
p.add_argument('--threads',type=int,default=8)
a=p.parse_args();d=a.destination.resolve()
if d.exists():p.error('Destination must not already exist.')
d.mkdir(parents=True)
for name in ['scripts','vendor','tests','config','inputs','prepared','expected_results']:
    shutil.copytree(root/name,d/name,ignore=shutil.ignore_patterns('__pycache__'))
for name in ['workspace.py','workspace_scope.json','protocol.json','requirements.txt','requirements.lock.txt','validate_results.py']:
    shutil.copy2(root/name,d/name)
for command in [['workspace.py','verify'],['workspace.py','run','--workers',str(a.workers),'--threads',str(a.threads)],['workspace.py','summarize'],['validate_results.py']]:
    subprocess.run([sys.executable,*command],cwd=d,check=True)
