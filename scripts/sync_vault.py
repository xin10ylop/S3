"""Sync the Backblaze B2 vault into data/. Credentials from .env via os.environ."""
import os, sys, concurrent.futures, pathlib

def load_env(path=os.path.join(os.path.dirname(__file__), "..", ".env")):
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k, v)

load_env()
import boto3
from botocore.config import Config

s3 = boto3.client(
    "s3",
    endpoint_url=os.environ["DS_ENDPOINT"],
    region_name=os.environ["DS_REGION"],
    aws_access_key_id=os.environ["DS_KEY"],
    aws_secret_access_key=os.environ["DS_SECRET"],
    config=Config(retries={"max_attempts": 8, "mode": "adaptive"}, max_pool_connections=32),
)
BUCKET = os.environ["DS_BUCKET"]
ROOT = pathlib.Path(os.path.join(os.path.dirname(__file__), "..", "data")).resolve()

def list_all():
    objs = []
    token = None
    while True:
        kw = dict(Bucket=BUCKET, MaxKeys=1000)
        if token:
            kw["ContinuationToken"] = token
        r = s3.list_objects_v2(**kw)
        objs.extend(r.get("Contents", []))
        if not r.get("IsTruncated"):
            break
        token = r["NextContinuationToken"]
    return objs

def download(obj):
    key, size = obj["Key"], obj["Size"]
    dest = ROOT / key
    if dest.exists() and dest.stat().st_size == size:
        return 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    s3.download_file(BUCKET, key, str(tmp))
    tmp.rename(dest)
    return size

if __name__ == "__main__":
    objs = list_all()
    total = sum(o["Size"] for o in objs)
    print(f"{len(objs)} objects, {total/1e9:.2f} GB total")
    if len(sys.argv) > 1 and sys.argv[1] == "list":
        for o in objs:
            print(f"{o['Size']:>12}  {o['Key']}")
        sys.exit(0)
    done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as ex:
        futs = {ex.submit(download, o): o for o in objs}
        for i, f in enumerate(concurrent.futures.as_completed(futs)):
            try:
                done += f.result()
            except Exception as e:
                print(f"FAIL {futs[f]['Key']}: {e}", flush=True)
            if (i + 1) % 100 == 0:
                print(f"{i+1}/{len(objs)} files, {done/1e9:.2f} GB new", flush=True)
    print("sync complete")
