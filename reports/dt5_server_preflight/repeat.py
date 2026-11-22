import hashlib, itertools, json, time, platform, sys, statistics
from pathlib import Path
import psutil
from sat_sim.telemetry_overview import read_telemetry_overview
base=Path(".tmp/million_fixture")
src=Path("results/telemetry.jsonl")
process=psutil.Process()
results=[]
for n in (10000,100000,1000000):
    root=base if n==1000000 else Path(f".tmp/dt5-prefix-{n}")
    if root!=base:
        (root/src).parent.mkdir(parents=True,exist_ok=True)
        with (base/src).open("rb") as original,(root/src).open("wb") as out:
            out.writelines(itertools.islice(original,n))
    with (root/src).open("rb") as f:
        sha=hashlib.file_digest(f,"sha256").hexdigest()
    for rep in range(5):
        for window in (False,True):
            lo=n//20
            args={"start_s":lo,"end_s":lo+10} if window else {}
            t=time.perf_counter()
            cpu=time.process_time()
            data=read_telemetry_overview(root,max_points=10000,fields=tuple("orbit.position_m_"+k for k in "xyz"),**args)
            wall=time.perf_counter()-t
            cpu=time.process_time()-cpu
            assert data["total"]==(101 if window else n)
            assert data["rows"][0]["sample_index"]==(lo*10 if window else 0)
            assert data["rows"][-1]["sample_index"]==((lo+10)*10 if window else n-1)
            results.append(dict(n=n,window=window,rep=rep,source_sha256=sha,selected=data["count"],seconds=wall,cpu_seconds=cpu,rss_after=process.memory_info().rss,response_json_bytes=len(json.dumps(data,separators=(",",":"),ensure_ascii=False).encode())))
    print(n,[(w,round(statistics.median(x["seconds"] for x in results if x["n"]==n and x["window"]==w),4)) for w in (False,True)],flush=True)
Path(".tmp/dt5-repeat-results.json").write_text(json.dumps(dict(platform=platform.platform(),python=sys.version,results=results),indent=2),encoding="utf-8")
