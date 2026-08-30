#!/usr/bin/env python3
"""Optional Maigret username search for a user-supplied public username. No login automation."""
from __future__ import annotations
import argparse, shutil, subprocess, sys

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('username'); ap.add_argument('--json',action='store_true'); args=ap.parse_args()
    cmd=shutil.which('maigret')
    if not cmd: raise SystemExit('Maigret is not installed. Install it locally to use this optional tool.')
    call=[cmd,args.username,'--site','GitHub,GitLab,Reddit,Medium']
    if args.json: call += ['--json','maigret_results.json']
    return subprocess.call(call)
if __name__=='__main__': raise SystemExit(main())
