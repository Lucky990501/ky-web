"""Operator-only: accept a password HASH on stdin; never accept/print a password."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.preview_customer_auth import provision

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--data-dir',type=Path,required=True);args=parser.parse_args()
    value=json.loads(sys.stdin.buffer.read(4096))
    if set(value)!={'operation_id','account','password_hash'}:raise ValueError('Invalid provisioning contract')
    os.environ['CUSTOMER_PREVIEW_MODE']='true'
    result=provision(args.data_dir,**value)
    print(json.dumps(result))

if __name__=='__main__':main()
