"""Run every suite. `--clean-home` runs them the way CI does.

CI was red on 25 consecutive commits while the local suite read ALL GREEN,
because 13 tests assert on the owner's own live data — his transcripts, his
goals, his git identity, his ~/.sendmail.conf — and a GitHub runner has none
of it. The gap is not detectable by running the tests where the data lives.

`--clean-home` points HOME at an empty directory, which reproduces the whole
class locally in about a minute instead of a 15-minute CI round trip.
"""
import glob, subprocess, sys, time, os, tempfile

SKILL = os.path.expanduser("~/.claude/skills/meditate")
os.chdir(SKILL)
clean = "--clean-home" in sys.argv
env = dict(os.environ)
if clean:
    home = tempfile.mkdtemp(prefix="suite-clean-home-")
    env["HOME"] = home
    print("clean HOME: %s  (the way CI sees it)" % home)
    # CI pip-installs the grading engine, so `import nidra` works there. This
    # machine resolves it from a checkout under the REAL home, which the
    # clean HOME hides — and the harness then reported 11 failures across
    # ask/converse/formation that CI does not have. A gate that cries wolf
    # gets ignored, so hand the engine over the way CI has it.
    sys.path.insert(0, SKILL)
    import paths as _paths
    _root = _paths.nidra_root()
    if _root:
        env["MEDITATE_NIDRA_ROOT"] = _root
        print("grading engine: %s  (CI pip-installs it; same reachability)" % _root)
    else:
        print("grading engine: absent here AND in the clean HOME — "
              "store-backed tests will skip, as they would on a machine without it")
fails=[]; slow=[]
files=sorted(glob.glob("test_*.py"))
t0=time.time()
for f in files:
    s=time.time()
    try:
        r=subprocess.run([sys.executable,f],capture_output=True,text=True,timeout=180,env=env)
        d=time.time()-s
        if d>20: slow.append((f,round(d,1)))
        if r.returncode!=0:
            tail=(r.stdout or r.stderr).strip().splitlines()[-3:]
            fails.append((f," / ".join(tail)))
    except subprocess.TimeoutExpired:
        fails.append((f,"TIMEOUT 180s"))
print("files: %d, wall %.0fs%s"%(len(files),time.time()-t0," (clean HOME)" if clean else ""))
print("SLOW:",slow)
print("FAILED %d"%len(fails) if fails else "ALL GREEN")
for f,m in fails: print("  %-28s %s"%(f,m))
sys.exit(1 if fails else 0)
