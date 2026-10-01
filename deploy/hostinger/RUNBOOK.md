# JARVIS on Hostinger: Runbook

How to operate the JARVIS stack on the Hostinger VPS and the Windows PC.

| Section | Added by |
|---|---|
| [Scope rules](#scope-rules) | C-11 |
| [Backup & Restore](#backup--restore) | C-11 (OP-07) |
| Deploy, rollback and the other-apps smoke check | C-12 (OP-08) adds these |

Shorthands used below. Run them in Git Bash on the PC.

```bash
VPS='ssh -i ~/.ssh/id_ed25519_hostinger -o BatchMode=yes root@187.127.123.235'
PCB=/c/Users/Matloob/JARVIS-backups   # in Git Bash use /c/... paths; a "C:\..." path breaks scp and tar
```

On the VPS (as root):

```bash
C='docker compose -p jarvis -f /opt/jarvis-agent/deploy/hostinger/docker-compose.yml --env-file /opt/jarvis-agent/deploy/hostinger/.env'
```

## Scope rules

Many unrelated production apps share this server. Follow the COORD-4 rules (plan.md, contract K-54):

- Touch only the JARVIS scope: Docker project `jarvis` (always `docker compose -p jarvis`), `/opt/jarvis-agent`, `/etc/nginx/conf.d/jarvis.conf`, `/etc/nginx/jarvis.htpasswd` and `/root/jarvis-backups`.
- Never: change UFW, touch another app's containers, volumes, vhosts, certificates or config, run `docker system|image|volume|builder prune`, run `docker compose down` without `-p jarvis`, restart nginx or the Docker daemon, or reboot the server.
- nginx changes: run `nginx -t && systemctl reload nginx` only.
- Never delete files or backups on the server without the user's explicit OK.
- Every change follows this order: `backup_vps.sh`, then the change, then `nginx -t` before any reload, then a health check, then an entry in done_highlight.md that includes the rollback command.

## Backup & Restore

### What gets backed up

**VPS:** `deploy/hostinger/backup_vps.sh [label]` writes `/root/jarvis-backups/<YYYY-MM-DD_HHMMZ>[-label]/`. The time is UTC. The folder is mode 700 and every file in it is mode 600.

| File | Source | Notes |
|---|---|---|
| `opt-jarvis-agent.tgz` | `/opt/jarvis-agent` without `stonix_ui/node_modules` | Members are `jarvis-agent/...`, so extract with `-C /opt`. Also holds the env files and `livekit.yaml`. |
| `jarvis.conf` | `/etc/nginx/conf.d/jarvis.conf` | |
| `jarvis.htpasswd` | `/etc/nginx/jarvis.htpasswd` | Copied only when present. |
| `hostinger.env` | `/opt/jarvis-agent/deploy/hostinger/.env` | Compose interpolation and the bridge `env_file`. |
| `bridge.env` | `/opt/jarvis-agent/deploy/hostinger/bridge.env` | Copied only when present (after C-ENV-1). |
| `root.env` | `/opt/jarvis-agent/.env` | Copied only when present. |
| `DEPLOYED_SHA` | `/opt/jarvis-agent/DEPLOYED_SHA` | Copied only when present (after C-12). |
| `volumes.tgz` | `/app/logs`, `/app/Jarvis_Outputs`, `/app/Jarvis_Shared`, `/app/data` of the bridge | Made by `docker exec jarvis-bridge tar`. If the bridge is down, a one-off `compose run` container makes it instead. The live SQLite files are left out when `bridge.db` was captured. |
| `bridge.db` | `/app/data/bridge.db` | Present only when that DB exists. Taken online with the SQLite backup API, passes `integrity_check`, then `docker cp`. |
| `images.txt` | Docker | Rollback tags with their image ids, plus the LiveKit image ref and id. |
| `docker-ps.txt`, `containers.txt` | Docker | State of project `jarvis`: image, health and mounts. No env values. |
| `MANIFEST.txt`, `SHA256SUMS` | | Summary with any warnings and errors, plus checksums for `sha256sum -c`. |

The script also tags the images that the `jarvis-bridge` and `jarvis-frontend` containers are running as `jarvis-bridge:rollback-<folder>` and `jarvis-frontend:rollback-<folder>`. These are normally the `:hostinger` images.

The script never deletes or changes an older backup. It prints `BACKUP_DIR=<folder>` as its last line, and it exits with 1 if any step failed. A failed run still keeps its partial folder.

**PC** (`scripts/ops/backup_local.py`, folder `%USERPROFILE%\JARVIS-backups\local` or `JARVIS_BACKUP_DIR`):

- `--now` writes `backup_<YYYYmmdd_HHMMSS>.zip`. It holds `.env`, `deploy/windows-local/.env`, `conversations/`, `chroma_db/`, `data/identity/`, `Jarvis_Outputs/`, `backups/checkpoints/`, `logs/*.log`, `logs/telemetry.jsonl`, `plan.md` and `done_highlight.md`, with paths relative to the repo root. SQLite files are copied with the online backup API. Rotation keeps the newest 30 `backup_<ts>.zip` files and never touches any other file.
- `--full-tree` writes `tree_<ts>.tar.gz`: the whole repo, including `.git`, without `.venv_312`, `stonix_ui/node_modules`, `pretrained_models` and caches. Add `--no-git` to also leave out `.git`. Rotation never deletes these archives.
- `--list` shows the local backups, the older one-off archives in `JARVIS-backups\` and the VPS mirrors in `JARVIS-backups\vps\`.

The VPS mirrors and the PC backups contain secrets (env files, htpasswd, tokens inside the tarballs). Keep them in `JARVIS-backups`, which is outside the repo, and never commit or share them.

### Known snapshots

| Snapshot | Made by | Image tags | Note |
|---|---|---|---|
| `2026-09-30_2340` | Manual emergency snapshot, taken before the token rotation | `jarvis-{bridge,frontend}:pre-emergency-2026-09-30_2340` | Different layout: `opt-jarvis-agent.tgz`, `nginx-jarvis.conf(.live)`, `docker-inspect.json` (contains env values), `bridge-logs/`, `bridge-Jarvis_Outputs/`, `bridge-Jarvis_Shared/`. Disaster recovery only (see below). |
| `2026-10-01_0825Z-first-scripted` | `backup_vps.sh` v1, first run | `jarvis-{bridge,frontend}:rollback-2026-10-01_0825Z-first-scripted` | State after the emergency fixes. |
| `2026-10-01_0826Z-verify` | `backup_vps.sh` v1, verify run | `jarvis-{bridge,frontend}:rollback-2026-10-01_0826Z-verify` | Same state as above. |

Treat the `pre-emergency` snapshot as disaster recovery only. Its code archive has the old, revoked token and `5100` published on all interfaces, and its frontend image has the revoked token in the JS bundle. Restoring it undoes the 30 Sep security fixes, so use it only to recover data or as a last resort.

### Take a VPS backup

```bash
$VPS 'bash /opt/jarvis-agent/deploy/hostinger/backup_vps.sh pre-deploy'
```

- The label is optional: 1-40 characters from `A-Z a-z 0-9 . _ -`. Two runs in the same minute get a `-2`, `-3`, ... suffix.
- From C-12 onward, `deploy_from_pc.sh` ships this script. Before that, copy it once by hand:
  `scp -i ~/.ssh/id_ed25519_hostinger deploy/hostinger/backup_vps.sh root@187.127.123.235:/opt/jarvis-agent/deploy/hostinger/backup_vps.sh`

### Mirror VPS backups to the PC

Copy a single snapshot and check it:

```bash
mkdir -p "$PCB/vps"
TS=2026-10-01_0826Z-verify
scp -p -r -i ~/.ssh/id_ed25519_hostinger -o BatchMode=yes "root@187.127.123.235:/root/jarvis-backups/$TS" "$PCB/vps/"
(cd "$PCB/vps/$TS" && sha256sum -c --quiet SHA256SUMS && echo MIRROR_OK)
```

Copy every snapshot that is not on the PC yet. This never overwrites or deletes anything:

```bash
for TS in $($VPS 'ls -1 /root/jarvis-backups | grep "^2"'); do
  [ -e "$PCB/vps/$TS" ] || scp -p -r -q -i ~/.ssh/id_ed25519_hostinger -o BatchMode=yes \
    "root@187.127.123.235:/root/jarvis-backups/$TS" "$PCB/vps/"
done
```

The hand-made `2026-09-30_2340` snapshot has no `SHA256SUMS`. Check it by comparing `find . -type f -print0 | sort -z | xargs -0 sha256sum` on both sides.

### Restore drill (read-only; run after every backup)

```bash
$VPS 'D=$(ls -1d /root/jarvis-backups/2* | tail -1); tar --compare -z -f $D/opt-jarvis-agent.tgz -C /opt 2>&1 | grep -v DEPLOYED_SHA | head; echo RESTORE_DRILL_DONE'
$VPS 'D=$(ls -1d /root/jarvis-backups/2* | tail -1); cd $D && sha256sum -c --quiet SHA256SUMS && tar -tzf volumes.tgz >/dev/null && echo ARCHIVES_OK'
```

If nothing is printed before `RESTORE_DRILL_DONE`, the live `/opt/jarvis-agent` matches the backup. Right after a deploy, a list of differences is expected.

### Restore (restore only what is broken)

First take a fresh backup (`backup_vps.sh pre-restore`) so the restore itself can be undone. Then set `B=/root/jarvis-backups/<snapshot>`. After every restore, run [the health check](#health-check-after-a-restore).

**1. Images.** This is the fastest way back from a bad deploy.

```bash
docker tag jarvis-bridge:rollback-<snapshot>   jarvis-bridge:hostinger
docker tag jarvis-frontend:rollback-<snapshot> jarvis-frontend:hostinger
$C up -d --no-build --no-deps ui-bridge frontend
```

If the bad deploy also changed `docker-compose.yml`, restore the code (step 2) first.

LiveKit has no rollback tag. `images.txt` records its image ref and id (`livekit<TAB>ref<TAB>id`). To go back:

1. Restore the code so that compose names the old ref.
2. Run `docker tag <recorded id> <recorded ref>`.
3. Run `$C up -d --no-deps livekit`. This briefly drops live voice sessions.

If `docker image inspect <id>` fails, that image is gone; run `docker pull <ref>` instead.

**2. Code** (`/opt/jarvis-agent`). As root, ownership and modes are restored too:

```bash
tar -xzf $B/opt-jarvis-agent.tgz -C /opt
```

Extracting overwrites only files that are in the archive. Files created after the backup stay in place. This lists them:

```bash
comm -13 <(tar -tzf $B/opt-jarvis-agent.tgz | sed 's#/$##' | sort) \
         <(cd /opt && find jarvis-agent -path jarvis-agent/stonix_ui/node_modules -prune -o -print | sort)
```

Then recreate the containers with the restored compose file, using step 1 or a rebuild.

**3. nginx.** Use the live owners and modes, test, then reload. Never restart nginx.

```bash
install -m 644 -o root -g root     $B/jarvis.conf     /etc/nginx/conf.d/jarvis.conf
install -m 640 -o root -g www-data $B/jarvis.htpasswd /etc/nginx/jarvis.htpasswd
nginx -t && systemctl reload nginx
```

For the `2026-09-30_2340` snapshot, use `nginx-jarvis.conf.live` instead. It has no htpasswd.

**4. Env files.**

```bash
install -m 600 $B/hostinger.env /opt/jarvis-agent/deploy/hostinger/.env
[ ! -f $B/bridge.env ] || install -m 600 $B/bridge.env /opt/jarvis-agent/deploy/hostinger/bridge.env
[ ! -f $B/root.env ]   || install -m 600 $B/root.env   /opt/jarvis-agent/.env
$C up -d --no-build --no-deps ui-bridge
```

Compose recreates the bridge when its env changes. Build args are baked into the frontend image, so for the frontend either roll the image back (step 1) or rebuild it. If the LiveKit keys changed, also run `$C up -d --no-deps livekit`.

**5. Volumes** (bridge logs, outputs, shared files and data):

```bash
$C stop ui-bridge
$C run --rm --no-deps -T --entrypoint tar ui-bridge -xzf - -C /app < $B/volumes.tgz
$C start ui-bridge
```

To restore `bridge.db`, the backup must contain `bridge.db` and the compose file must mount `jarvis_bridge_data` at `/app/data` (from C-STATE-1 onward). Run this while the bridge is stopped, between `stop` and `start` above. The current DB files are renamed aside, never deleted:

```bash
$C run --rm --no-deps -T --entrypoint sh ui-bridge -c 'set -e; cd /app/data; ts=$(date -u +%Y%m%d%H%M%S);
  for f in bridge.db bridge.db-wal bridge.db-shm; do [ ! -e "$f" ] || mv "$f" "$f.pre-restore-$ts"; done;
  cat > bridge.db' < $B/bridge.db
```

For the `2026-09-30_2340` snapshot, copy its folders while the bridge is running, for example `docker cp $B/bridge-logs/. jarvis-bridge:/app/logs/`. Use the same pattern for `bridge-Jarvis_Outputs` and `bridge-Jarvis_Shared`.

#### Health check after a restore

```bash
curl -fsS http://127.0.0.1:5101/health; echo
curl -s -o /dev/null -w 'frontend %{http_code}\n' http://127.0.0.1:5100/
docker ps --filter label=com.docker.compose.project=jarvis --format '{{.Names}} {{.Status}}'
docker ps -q | wc -l   # total running containers: compare with the number before the change (other apps untouched)
```

### Prune old VPS backups (only with the user's OK)

```bash
$VPS 'bash /opt/jarvis-agent/deploy/hostinger/backup_vps.sh --prune-older-than-days 60'            # dry run: lists only
$VPS 'bash /opt/jarvis-agent/deploy/hostinger/backup_vps.sh --prune-older-than-days 60 --confirm'  # after the user's OK
```

Prune follows these rules:

- It only looks at folders the script made (`YYYY-MM-DD_HHMMZ[-label]`). Hand-made snapshots such as `2026-09-30_2340` are never touched.
- The newest 3 scripted backups are always kept.
- The `rollback-<folder>` tags of a pruned folder are removed with `docker image rm`, never forced. A tag that a container still uses is kept.

### PC backups and restore

```bash
.venv_312/Scripts/python.exe scripts/ops/backup_local.py --now        # data zip, then rotation (keep 30)
.venv_312/Scripts/python.exe scripts/ops/backup_local.py --full-tree  # whole repo tar.gz
.venv_312/Scripts/python.exe scripts/ops/backup_local.py --list
```

Restore on the PC:

1. Stop the agent first (close the `start-agent.bat` window).
2. Extract the zip into an empty folder, for example `Expand-Archive <zip> -DestinationPath $env:TEMP\jarvis-restore` in PowerShell.
3. Copy back only the files you need. Paths inside the zip are relative to the repo root. Compare `.env` before overwriting it.

For a full tree, extract into a new folder and compare before copying:

```bash
mkdir -p /c/Users/Matloob/Desktop/jarvis-restore
tar -xzf "$PCB/local/tree_<ts>.tar.gz" -C /c/Users/Matloob/Desktop/jarvis-restore
```
