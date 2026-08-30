#!/usr/bin/env python3
"""Optional FORGE bridge. Requires FORGE installed locally; it does not install or execute unknown binaries."""
from __future__ import annotations
import argparse, shutil, subprocess, sys

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('input_csv'); ap.add_argument('-o','--output',default='forge_enriched.csv'); ap.add_argument('--mode',default='both',choices=['email','ai','both']); args=ap.parse_args()
    forge=shutil.which('forge')
    if not forge: raise SystemExit('FORGE CLI not found. Install the open-source forge-enrichment package locally first.')
    cmd=[forge,'enrich','--file',args.input_csv,'--output',args.output,'--mode',args.mode,'--resume']
    print('Running:', ' '.join(cmd)); return subprocess.call(cmd)
if __name__=='__main__': raise SystemExit(main())
