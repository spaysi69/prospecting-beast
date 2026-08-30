#!/usr/bin/env python3
"""Generate public-web LinkedIn X-ray queries from the Prospecting Beast role list."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core import title_dictionary, norm_domain
from app.osint_tools import xray_matrix


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("company")
    ap.add_argument("--location", default="")
    ap.add_argument("--industry", default="")
    ap.add_argument("--mode", choices=["f", "nf", "both"], default="f")
    ap.add_argument("--limit", type=int, default=30)
    args = ap.parse_args()
    roles = list(title_dictionary(args.mode).keys())
    domain = norm_domain(args.company)
    company = domain
    rows = xray_matrix(company, [domain] if domain else [], roles, args.location, args.industry)
    print(json.dumps(rows[:args.limit], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
