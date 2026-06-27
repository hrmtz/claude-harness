#!/usr/bin/env python3
"""Tests for sanada_autobackup.sh (gh #35). Uses an isolated HOME so real backups
are untouched. 'backed up' == a file matching the target appears under
<HOME>/sanada_backup_persistent/. Run: python3 plugins/harness-core/tests/test_sanada_autobackup.py"""
import subprocess, json, os, tempfile, shutil, glob, sys

HOOK = os.path.join(os.path.dirname(__file__), "..", "hooks", "sanada_autobackup.sh")

def run(cmd, files=("target.txt",), make=True):
    home = tempfile.mkdtemp(); work = tempfile.mkdtemp()
    try:
        if make:
            for f in files: open(os.path.join(work, f), "w").write("DATA-" + f)
        env = dict(os.environ, HOME=home)
        subprocess.run(["bash", HOOK], input=json.dumps({"tool_input": {"command": cmd}, "cwd": work}),
                       capture_output=True, text=True, env=env)
        backed = glob.glob(os.path.join(home, "sanada_backup_persistent", "**"), recursive=True)
        names = {os.path.basename(p) for p in backed if os.path.isfile(p)}
        return names
    finally:
        shutil.rmtree(home, ignore_errors=True); shutil.rmtree(work, ignore_errors=True)

ok = True
def expect(cmd, want_backup, label, target="target.txt", **kw):
    global ok
    names = run(cmd, **kw)
    got = target in names
    if got != want_backup:
        ok = False
        print(f"  ✗ FAIL want_backup={want_backup} got={got} :: {label}: {cmd}")

# --- SHOULD back up (real destructive ops on an existing named file) ---
expect("rm target.txt",            True,  "rm")
expect("rm -rf target.txt",        True,  "rm -rf")
expect("cat /etc/hostname > target.txt", True, "redirect overwrite")
expect("cat /etc/hostname >| target.txt", True, "clobber redirect >|")
expect("sed -i s/x/y/ target.txt", True,  "sed -i")
expect("truncate -s 0 target.txt", True,  "truncate")
expect("mv other target.txt",      True,  "mv overwrite dest")
expect("cp other target.txt",      True,  "cp overwrite dest")
expect("dd if=/dev/zero of=target.txt", True, "dd of=")
expect("tee target.txt",           True,  "tee overwrite")
expect("ls; rm target.txt",        True,  "chained rm segment")

# --- should NOT back up (read / mention / append / harmless / nonexistent / glob) ---
expect("grep rm target.txt",       False, "grep (read, not destroy)")
expect('echo "rm target.txt"',     False, "echo mention")
expect("cat other >> target.txt",  False, "append (>>) not overwrite")
expect("tee -a target.txt",        False, "tee -a append")
expect("ls -la target.txt",        False, "ls metadata")
expect("cat target.txt",           False, "cat read")
expect("rm nonexistent.txt",       False, "nonexistent target", make=False)
expect("rm *.tmp",                 False, "glob skipped")

# --- dir-dest overwrite + private perms (codex #35 REVISE) ---
def run_setup(cmd, builder):
    home = tempfile.mkdtemp(); work = tempfile.mkdtemp()
    try:
        builder(work)
        subprocess.run(["bash", HOOK], input=json.dumps({"tool_input": {"command": cmd}, "cwd": work}),
                       capture_output=True, text=True, env=dict(os.environ, HOME=home))
        root = os.path.join(home, "sanada_backup_persistent")
        files = [p for p in glob.glob(os.path.join(root, "**"), recursive=True) if os.path.isfile(p)]
        names = {os.path.basename(p) for p in files}
        perms_ok = all((os.stat(p).st_mode & 0o077) == 0 for p in files) and \
                   all((os.stat(d).st_mode & 0o077) == 0 for d in glob.glob(os.path.join(root, "auto_*")) if os.path.isdir(d))
        return names, perms_ok
    finally:
        shutil.rmtree(home, ignore_errors=True); shutil.rmtree(work, ignore_errors=True)

def build(*paths):
    def b(w):
        for p in paths:
            fp = os.path.join(w, p)
            if os.path.dirname(fp): os.makedirs(os.path.dirname(fp), exist_ok=True)
            open(fp, "w").write("D")
    return b

names, perms = run_setup("cp src dest", build("src", "dest/src"))
if "src" not in names: ok = False; print("  ✗ FAIL cp-into-dir should back up dest/src")
names, perms = run_setup("rm secret.env", build("secret.env"))
if not perms: ok = False; print("  ✗ FAIL backup dir/files must be private (go-rwx)")

print("sanada_autobackup: ALL PASS ✓" if ok else "sanada_autobackup: FAILURES ✗")
sys.exit(0 if ok else 1)
