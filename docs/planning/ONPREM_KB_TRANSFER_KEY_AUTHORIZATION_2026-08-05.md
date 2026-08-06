# Restricted knowledge-base transfer key authorization

Status: **LOCAL KEY AND PLAN READY; SERVER AUTHORIZATION NOT PERFORMED**.

Public key path:
`C:\Users\tedsp\.ssh\lcdash_kb_transfer_20260805.pub`

ED25519 fingerprint:
`SHA256:1BndDO17kw/1aJY3WUszoqpMocvlZsUM4CsIbrojj5Y`

Allowlist: `ONPREM_KB_TRANSFER_ALLOWLIST_2026-08-05.txt`

- Lines: 164 unique safe relative paths
- Mindshare: 131
- CentralSquare: 33
- SHA-256: `91ce127538d0ab80931e2b5bfb11fe4d9155ed8ab5336a41194dc47e7ff638e3`

The administrator must first place the reviewed allowlist at
`/tmp/ONPREM_KB_TRANSFER_ALLOWLIST_2026-08-05.txt` using the administrator's
existing approved management channel. Do not use the new transfer key for that
step. Then run this exact block locally on `.227`:

```bash
set -eu
staged=/tmp/ONPREM_KB_TRANSFER_ALLOWLIST_2026-08-05.txt
installed=/etc/lcdash/kb-transfer-allowlist-20260805.txt
expected=91ce127538d0ab80931e2b5bfb11fe4d9155ed8ab5336a41194dc47e7ff638e3
test "$(sha256sum "$staged" | awk '{print $1}')" = "$expected"
test "$(wc -l < "$staged")" -eq 164
test "$(sort -u "$staged" | wc -l)" -eq 164
! grep -Eq '(^/|(^|/)\.\.(/|$)|^[-]|[[:cntrl:]])' "$staged"
sudo install -d -m 755 -o root -g root /etc/lcdash
sudo install -m 0444 -o root -g root "$staged" "$installed"
sudo install -d -m 700 -o administrator -g "$(id -gn administrator)" /home/administrator/.ssh
sudo tee -a /home/administrator/.ssh/authorized_keys >/dev/null <<'LCDASH_KB_TRANSFER_KEY'
restrict,expiry-time="20260807000000Z",command="/usr/bin/tar -C /srv/lcdash-data/documents --verbatim-files-from --no-recursion -cf - -T /etc/lcdash/kb-transfer-allowlist-20260805.txt" ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAICIA8c8/nzxluBtgfonJnVdTGLSuzOVVyi/olOofYEDW lcdash-kb-allowlist-transfer-exp-2026-08-06
LCDASH_KB_TRANSFER_KEY
sudo chown administrator:"$(id -gn administrator)" /home/administrator/.ssh/authorized_keys
sudo chmod 600 /home/administrator/.ssh/authorized_keys
```

The forced command streams a GNU tar archive to stdout from exactly the
root-owned allowlist. `--verbatim-files-from` preserves spaces literally and
prevents filenames from being interpreted as tar options. `--no-recursion`
prevents directory traversal. OpenSSH `restrict` disables arbitrary commands,
PTY, forwarding, agent forwarding, and X11. The key expires at
2026-08-07 00:00:00 UTC.

After the one authorized transfer attempt, remove the authorization and
allowlist locally on `.227`:

```bash
sudo sed -i '/lcdash-kb-allowlist-transfer-exp-2026-08-06$/d' /home/administrator/.ssh/authorized_keys
sudo rm -f /etc/lcdash/kb-transfer-allowlist-20260805.txt /tmp/ONPREM_KB_TRANSFER_ALLOWLIST_2026-08-05.txt
```

This preparation contacted no server, read no document content, performed no
upload, and changed no AWS, Bedrock, vector, or RAG resource.
