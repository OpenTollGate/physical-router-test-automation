import subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor
ROOT="/home/c08r4d0r/worktrees/fleet-consolidated"
UNITS=[("gl-mt3000","BG046H5F"),("comfast","BG046H14"),("tollgate-af92","BH00D6DL")]
def work(name, serial):
    p=f"/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_{serial}-if00-port0"
    t=time.perf_counter()
    r=subprocess.run([sys.executable,f"{ROOT}/scripts/router-serial.py","--port",p,"exec",
        "cat /tmp/sysinfo/model; uptime; cat /proc/meminfo | head -1; wc -l < /proc/net/nf_conntrack 2>/dev/null || echo 0"],
        capture_output=True,text=True,timeout=120,cwd=ROOT)
    return name, time.perf_counter()-t, r.returncode
print("serial:")
t0=time.perf_counter()
for n,s in UNITS:
    nm,el,rc=work(n,s); print(f"  {nm:14} {el:6.2f}s rc={rc}")
ser=time.perf_counter()-t0
print(f"  {'TOTAL':14} {ser:6.2f}s\n")
print("parallel:")
t0=time.perf_counter()
with ThreadPoolExecutor(max_workers=3) as ex:
    res=list(ex.map(lambda u: work(*u), UNITS))
par=time.perf_counter()-t0
for nm,el,rc in res: print(f"  {nm:14} {el:6.2f}s rc={rc}")
print(f"  {'TOTAL':14} {par:6.2f}s\n")
print(f"serial   {ser:6.2f}s")
print(f"parallel {par:6.2f}s")
print(f"speedup  {ser/par:6.2f}x over {len(UNITS)} real routers")
