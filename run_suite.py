import glob, subprocess, sys, time, os
os.chdir(os.path.expanduser("~/.claude/skills/meditate"))
fails=[]; slow=[]
files=sorted(glob.glob("test_*.py"))
t0=time.time()
for f in files:
    s=time.time()
    try:
        r=subprocess.run([sys.executable,f],capture_output=True,text=True,timeout=180)
        d=time.time()-s
        if d>20: slow.append((f,round(d,1)))
        if r.returncode!=0:
            tail=(r.stdout or r.stderr).strip().splitlines()[-3:]
            fails.append((f," / ".join(tail)))
    except subprocess.TimeoutExpired:
        fails.append((f,"TIMEOUT 180s"))
print("files: %d, wall %.0fs"%(len(files),time.time()-t0))
print("SLOW:",slow)
print("FAILED %d"%len(fails) if fails else "ALL GREEN")
for f,m in fails: print("  %-28s %s"%(f,m))
