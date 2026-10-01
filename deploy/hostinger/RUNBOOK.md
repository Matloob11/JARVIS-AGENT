# JARVIS on Hostinger: Runbook

How to operate the JARVIS stack on the Hostinger VPS and the Windows PC.

| Section | Added by |
|---|---|
| [Scope rules](#scope-rules) | C-11 |
| [Deploy (Claude only)](#deploy-claude-only), including [the frontend rule](#the-frontend-rule-2026-10-02) | C-12 (OP-08) |
| [Rollback](#rollback) | C-12 (OP-08) |
| [Other-apps smoke check](#other-apps-smoke-check) | C-12 (OP-08) |
| [Maintenance window checklist](#maintenance-window-checklist) | C-12 (OP-08) |
| [Adding a JARVIS service](#adding-a-jarvis-service) | C-12 (OP-08) |
| [Testing the deploy tooling](#testing-the-deploy-tooling) | C-12 (OP-08) |
| [Backup & Restore](#backup--restore) | C-11 (OP-07) |

Shorthands used below. Run them in Git Bash on the PC.

```bash
VPS='ssh -i ~/.ssh/id_ed25519_hostinger -o BatchMode=yes root@187.127.123.235'
PCB=/c/Users/Matloob/JARVIS-backups   # in Git Bash use /c/... paths; a "C:\..." path breaks scp and tar
PY=.venv_312/Scripts/python.exe
```

On the VPS (as root):

```bash
C='docker compose -p jarvis -f /opt/jarvis-agent/deploy/hostinger/docker-compose.yml --env-file /opt/jarvis-agent/deploy/hostinger/.env'
T=/root/jarvis-backups/tools   # deploy.sh, rollback.sh, smoke_other_apps.sh, backup_vps.sh as of the last deploy
```

With `$C` by hand: always name the services, use `up -d --no-deps <services>`, and never `down`, `--remove-orphans` or a prune.

## Scope rules

Many unrelated production apps share this server. Follow the COORD-4 rules (plan.md, contract K-54):

- Touch only the JARVIS scope: Docker project `jarvis` (always `docker compose -p jarvis`), `/opt/jarvis-agent`, `/etc/nginx/conf.d/jarvis.conf`, `/etc/nginx/jarvis.htpasswd` and `/root/jarvis-backups`.
- Never: change UFW, touch another app's containers, volumes, vhosts, certificates or config, run `docker system|image|volume|builder prune`, run `docker compose down` without `-p jarvis`, restart nginx or the Docker daemon, or reboot the server.
- nginx changes: run `nginx -t && systemctl reload nginx` only.
- Never delete files or backups on the server without the user's explicit OK.
- Every change follows this order: `backup_vps.sh`, then the change, then `nginx -t` before any reload, then a health check, then an entry in done_highlight.md that includes the rollback command.
- `deploy.sh` and `rollback.sh` follow that order by themselves, so use them instead of hand-made changes.

## Deploy (Claude only)

Deploys go only through `deploy/hostinger/deploy_from_pc.sh`, only from the committed HEAD, and only for the services named on the command line (K-54). Cursor never deploys; it asks with `REQ-DEPLOY` in plan.md section 11.

```bash
bash deploy/hostinger/deploy_from_pc.sh --dry-run --services ui-bridge     # 1. offline: nothing connects
bash deploy/hostinger/deploy_from_pc.sh --check --services ui-bridge       # 2. optional: dry run on the VPS
bash deploy/hostinger/deploy_from_pc.sh --services ui-bridge [--nginx]     # 3. deploy
```

- `--services` is required. List compose service names, separated by spaces or commas (`ui-bridge`, `livekit`, `frontend`, later `agent` and `agent-svc`), or give `none` to ship the code only.
  - Only the named services are pulled or built and recreated, always with `docker compose -p jarvis up -d --no-deps --no-build --pull never <services>`.
  - No other container is touched: not JARVIS's own, and not anyone else's.
- `--nginx` also installs `deploy/hostinger/jarvis.conf` as `/etc/nginx/conf.d/jarvis.conf`, runs `nginx -t`, then `systemctl reload nginx`. If the test fails, the old file goes back and nothing is reloaded. An unchanged file is not reloaded.
- Only commit `HEAD` is shipped (`git archive`, never the working tree, with LF line endings forced).
  - `HEAD` is read once at the start. The archive, the file list and the service check all use that commit id, so a commit Cursor makes meanwhile is never shipped under the recorded SHA.
  - Uncommitted changes in the shipped paths make it refuse.
  - `--allow-dirty` ships HEAD anyway and lists those files as NOT deployed. Cursor's work in progress in `stonix_ui/` often needs it; read the list.
- Shipped paths: `deploy`, `src`, `services`, `stonix_ui`, `scripts`, `.dockerignore` and the root `requirements*.txt`.
  - Files on the VPS that the commit does not have are kept, and listed in `<backup>/extra-files.txt`.
  - Env files are never shipped or overwritten.

### The frontend rule (2026-10-02)

The live frontend is an older build that still logs in with the legacy token. The UI in HEAD needs the cookie login of C-17 to C-22, which is not live yet.

- Do not name `frontend` in `--services` until C-21 (the first deploy of the login UI).
- `deploy.sh` never rebuilds or restarts the frontend unless it is named. Shipping `stonix_ui/` with the code is harmless.
- `ui-bridge` can be deployed alone: `--no-deps` leaves the frontend and LiveKit running as they are.

### What deploy.sh does on the VPS

`deploy_from_pc.sh` uploads the archive to `/root/jarvis-backups/runs/<run>/` and checks its sha256. It then runs the `deploy.sh` from that upload, which is the new version (the copy in `/opt/jarvis-agent` can be older). Before anything changes, the preflight:

- validates the new compose file against the live env files and refuses unknown service names;
- refuses an image build with less than 5 GB free;
- before a `livekit` deploy, checks that `LIVEKIT_API_KEY` and `LIVEKIT_API_SECRET` in `.env` match the `keys:` entry of the live `livekit.yaml`. A recreated LiveKit takes its keys from `LIVEKIT_KEYS`. The values are compared, never shown.

| Step | What | On failure |
|---|---|---|
| lock | `flock -n /root/jarvis-backups/.deploy.lock`: one deploy or rollback at a time | exit 1, nothing changed |
| tools | copies the four scripts to `/root/jarvis-backups/tools/`, which a code rollback never touches | |
| backup | `backup_vps.sh pre-deploy-<sha>` | exit 1, nothing changed |
| record | `<backup>/deploy.env` and `deploy-images.txt` (container, image ref and image id of each named service); `jarvis-*` images also get `:rollback-<backup>` | |
| smoke (pre) | `smoke_other_apps.sh > <backup>/smoke-pre.txt` | exit 1, nothing changed |
| install | the archive into `/opt/jarvis-agent`, then `DEPLOYED_SHA`; `livekit.yaml` is held back unless `livekit` is deployed (see below) | rollback |
| perms | env files and `livekit.yaml` 600, `/opt/jarvis-agent` 750, no world-writable files | warning |
| check | `docker compose config -q` | rollback |
| images | `pull --policy missing --ignore-buildable` and `build` for the named services; the running containers keep serving | code put back, exit 1 |
| nginx | only with `--nginx`; the full `nginx -t` output goes to `<backup>/nginx-test.txt` (root-only) and only its `jarvis.conf` lines are shown, because the rest can name other apps | code put back, exit 1 |
| recreate | `up -d --no-deps --no-build --pull never <services>` | rollback |
| gate | the health gate below, plus warnings for the cert (under 14 days), the Docker disk (under 10 GB) and a LiveKit restart hazard | rollback |
| smoke (post) | compare with `smoke-pre.txt`; a regression must still be there after a 20 s re-check | rollback |

Why `livekit.yaml` is held back: the sanitized file has no `keys:` line. A LiveKit container that is not recreated still has no `LIVEKIT_KEYS`, so it would fail on its next restart if it found the new file.

Health gate:

| Service | Container | HTTP probes |
|---|---|---|
| ui-bridge | healthy within 120 s | `127.0.0.1:5101/health` 200, `https://<domain>/vortex/health` 200 |
| frontend | healthy within 90 s | `127.0.0.1:5100/` 200, `https://<domain>/` 200 or 401 |
| livekit | running steadily within 60 s (it has no healthcheck) | `127.0.0.1:7880/` 200, `https://<domain>/rtc/validate` 401 |
| any other service | healthy within 120 s (240 s for `agent` and `agent-svc`); steadily running if it has no healthcheck | none yet |

Probes through nginx use `--resolve <domain>:443:127.0.0.1`. The JARVIS services that were not deployed get quick checks that only warn.

The automatic rollback:

- puts back the code (env files excluded) and, if it was changed, `jarvis.conf`;
- points each deployed service's image back at the image recorded before the deploy. The image name comes from that service's own entry in the restored compose file (`config --format json`), never from `config --images <svc>`, which also lists its `depends_on` images in random order;
- re-points only JARVIS's own tags (`jarvis-*`). A third-party tag such as `livekit/livekit-server:v1.13.7` only goes back to the image it named before; it is never moved onto another version;
- stops a service that this deploy created for the first time;
- recreates only the services the deploy touched, then reruns the gate.

`--no-auto-rollback` prints the rollback command instead of running it.

One case the automatic rollback cannot undo: the installed compose file already names another LiveKit version than the running container (code shipped earlier with `--services none`), and then a `livekit` deploy fails. Going back would mean moving the version tag, so it reports exit 3 instead. The preflight warns about this before anything changes. The fix is `rollback.sh <older backup> --code`.

Exit codes of `deploy.sh` and `deploy_from_pc.sh`:

| Code | Meaning |
|---|---|
| 0 | deployed and healthy |
| 1 | refused, or failed before any live change: the running services were not changed |
| 2 | a gate failed, and the automatic rollback restored the pre-deploy state |
| 3 | the rollback failed or was disabled: act now (the command is printed) |
| 5 | (PC only) the connection was lost; the deploy goes on on the VPS |
| 6 | (PC only) deployed, but the PC-side checks failed |
| 64 | usage error |

### Following a deploy, and a dropped connection

The run on the VPS is detached from the ssh session. Ctrl-C or a dropped link stops only the view, never the deploy, so there is no half-done deploy. To follow it again:

```bash
bash deploy/hostinger/deploy_from_pc.sh --attach <run id>     # the run id is printed at the start
$VPS 'R=/root/jarvis-backups/runs/<run id>; tail -n +1 -f --pid=$(cat $R/pid) $R/run.log; cat $R/rc'
```

To stop a running deploy on purpose, run `$VPS 'kill $(cat /root/jarvis-backups/runs/<run id>/pid)'`. It then rolls back whatever it had changed.

`deploy.sh` and `rollback.sh` with `--foreground` (on the VPS) stay attached. Ctrl-C, or the hang-up of an interactive (`ssh -t`) session, stops the run, which then rolls back what it changed. The run writes only to `runs/<run>/run.log`, and a background `tail` shows it, so an output that goes away (a plain `ssh host cmd` that drops) never kills it half-way: it finishes, and `rc` and `run.log` record the result.

### After a deploy

- The last lines are `RESULT=ok` and the rollback command. Put that rollback command in the done_highlight.md entry (K-54).
- `$VPS 'cat /opt/jarvis-agent/DEPLOYED_SHA'` equals `git rev-parse --short HEAD`.
- `$VPS 'tail -3 /root/jarvis-backups/deploy.log'` shows one line per deploy and rollback.
- The PC-side checks ran: `/vortex/health` gave 200, and `/` gave 401 (basic auth on) or 200.

State files in `/root/jarvis-backups`:

- `deploy.log`
- `LAST_DEPLOY`
- `tools/`
- `runs/<run>/`: `shipped.tar.gz`, `run.log`, `rc` and `pid`
- in each backup folder: `deploy.env`, `deploy-images.txt`, `smoke-pre.txt`, `smoke-post.txt` and `extra-files.txt`, plus `nginx-test.txt` when `nginx -t` ran. Do not print `nginx-test.txt` into a chat or a tracked file: it can name other apps.

### On the VPS without the PC

```bash
bash $T/deploy.sh --gate-only                       # health gate only (read-only)
bash $T/deploy.sh --services ui-bridge --dry-run    # plan only
bash $T/deploy.sh --services ui-bridge              # rebuild and recreate from the code already in /opt/jarvis-agent
```

Use the copies in `$T`, not the ones in `/opt/jarvis-agent/deploy/hostinger/`. After a code rollback to a backup older than C-12, `/opt` holds the old `deploy.sh`, which rebuilds every service.

### First sync deploy (C-12 step 8)

The coordinator runs this in an announced window, because the LiveKit recreate drops live voice sessions:

1. `bash deploy/hostinger/deploy_from_pc.sh --dry-run --services 'ui-bridge livekit' --nginx`
2. `bash deploy/hostinger/deploy_from_pc.sh --check --services 'ui-bridge livekit' --nginx`. The LiveKit keys check runs here.
3. `bash deploy/hostinger/deploy_from_pc.sh --services 'ui-bridge livekit' --nginx`. There is no `frontend`, because of the frontend rule.
4. `$PY scripts/agent_ctl.py restart --by claude --reason OP-08`, because the LiveKit restart ends the room.
5. Verify:
   - `[ "$($VPS 'cat /opt/jarvis-agent/DEPLOYED_SHA')" = "$(git rev-parse --short HEAD)" ] && echo SHA_MATCH`
   - `$VPS 'D=$(ls -1d /root/jarvis-backups/2* | tail -1); diff $D/smoke-pre.txt $D/smoke-post.txt && echo OTHER_APPS_UNCHANGED'`
   - `$VPS 'grep -c ^keys: /opt/jarvis-agent/deploy/hostinger/livekit.yaml; docker inspect -f {{.Config.Image}} jarvis-livekit'` should print 0 and `livekit/livekit-server:v1.13.7`.
   - The user presses Voice in the UI and hears a reply.
6. Rollback drill (step 9): `$VPS 'bash /root/jarvis-backups/tools/rollback.sh --dry-run'`, then without `--dry-run`. It undoes the deploy, LiveKit version included, because that backup records the code. Check health, then go forward again with the undo command it printed (or redeploy HEAD as in step 3). Each of these recreates LiveKit, so run the agent restart of step 4 after each one. A plain backup from before this deploy, such as `2026-10-01_0826Z-verify`, needs `--code`.

What a read-only check on 2026-10-02 found:

- `/opt/jarvis-agent` has no `DEPLOYED_SHA` yet.
- Its compose file still runs `livekit/livekit-server:latest` without `LIVEKIT_KEYS`, and its `livekit.yaml` still has a `keys:` line.
- The live `jarvis.conf` differs from the repo only in its header comment.
- `src/core/ui_bridge.py` is unchanged, but `services/**` (which the bridge image also copies) and `stonix_ui/**` differ.

## Rollback

```bash
$VPS 'bash /root/jarvis-backups/tools/rollback.sh --dry-run'          # plan: undo the last deploy
$VPS 'bash /root/jarvis-backups/tools/rollback.sh'                    # undo the last deploy
$VPS 'bash /root/jarvis-backups/tools/rollback.sh <backup folder>'    # back to that backup
$VPS 'bash /root/jarvis-backups/tools/rollback.sh --tag pre-emergency-2026-09-30_2340 --services ui-bridge'
```

- **The default (`--last`)** uses `/root/jarvis-backups/LAST_DEPLOY`. It names the backup that the last deploy took before it changed anything. That backup's `deploy.env` says which services, and whether the code and nginx, go back.
- **A named backup made by deploy.sh or rollback.sh** puts back exactly what that run changed.
- **Any other backup_vps.sh folder** puts back the services whose running image differs from its `containers.txt`. Add `--code`, `--nginx`, `--env` or `--services LIST` as needed.
- `--code` restores `/opt/jarvis-agent` from `opt-jarvis-agent.tgz`, but never the env files: a secret rotated after the backup stays rotated.
- `--env` restores `hostinger.env`, `bridge.env` and `root.env` from the backup (mode 600). Use it only when an env change was the problem.
- `--tag T` takes `<image>:T`, for JARVIS's own images (`jarvis-*`) only. The `pre-emergency` images hold the revoked token (see [Known snapshots](#known-snapshots)).
- Before it changes anything, `rollback.sh` checks each service against the compose file it will meet: a service it stops against the current file, a service it brings back against the target one (the backup's own with `--code`). So the printed undo of a rollback that stopped a new service (for example `agent`) works.
- Image tags follow the deploy rule: JARVIS's own `jarvis-*` tags are re-pointed; a third-party tag (LiveKit) is never moved onto another version. When the target state ran another LiveKit version than the compose file in effect names, `rollback.sh` refuses (exit 1, nothing changed) and asks for `--code`, which brings back the compose file that names it. After the first sync deploy (LiveKit pinned to v1.13.7), a rollback to a plain backup from before it therefore needs `--code`.
- Every rollback first runs `backup_vps.sh pre-rollback` and prints the command that undoes the rollback.
- Only the JARVIS services, `/opt/jarvis-agent` and `jarvis.conf` are touched, always with `up -d --no-deps --no-build`. The health gate and the smoke check run as in a deploy.
- Exit codes: 0 ok, 1 nothing changed, 3 failed after the changes started (act now), 64 usage error.
- Always run the copy in `/root/jarvis-backups/tools/`. A code rollback can bring back an old `deploy.sh` in `/opt`, and `rollback.sh` refuses to load anything but the v2 library.

## Other-apps smoke check

"Other apps" means everything on this server that is not JARVIS: the other nginx sites, the containers outside the compose project `jarvis`, and every TCP listener except JARVIS's own ports. Their names are never written into this public repo, its docs, plan.md, done_highlight.md or commits.

`smoke_other_apps.sh` discovers them at runtime and writes one line per item:

| Line | What |
|---|---|
| `site <hash> <code>` | one `GET /` per nginx server_name on 443 or 80, sent through the local nginx (`curl --resolve`, 8 s; 000 means no answer) |
| `ctr <hash> <state> <start-hash>` | state is healthy, up, starting, unhealthy, restarting, paused or other; the start hash changes when the container restarts |
| `listen <hash>` | a TCP listener from `ss -tln` |
| `nginx-test ok` (or `fail`) | whether `nginx -T` passes |

- Every name is a salted SHA-256 hash. The salt is in `/root/jarvis-backups/.smoke-salt` (mode 600). The output shows only counts, states and status codes.
- The check is passive. It sends one `GET /` per site, as any visitor would, and never connects to another app's own ports.

```bash
$VPS 'bash /root/jarvis-backups/tools/smoke_other_apps.sh > /root/jarvis-backups/smoke-now.txt'   # summary on stderr
$VPS 'cd /root/jarvis-backups && bash tools/smoke_other_apps.sh --compare <backup>/smoke-pre.txt smoke-now.txt'
```

`--compare` exits with:

- 2 for a regression: a site turned 5xx or 000, a container turned unhealthy, restarting or paused, or `nginx -T` started failing;
- 1 when something changed without a regression (WARN and INFO lines);
- 0 when nothing changed.

`deploy.sh` and `rollback.sh` treat a regression that is still there after a 20 s re-check as a failed gate.

## Maintenance window checklist

Use this before a change that recreates `livekit`, changes `jarvis.conf` or deploys a new service:

1. Tell the user. Live voice drops for about a minute when LiveKit restarts, and the PC agent must be restarted after it.
2. Run the dry run, then `--check`.
3. Check the disk. Builds are refused below 5 GB free and warned about below 10 GB. Never prune on this shared server; ask the user.
4. Deploy. Watch `RESULT=ok`, the gate and the smoke result.
5. If LiveKit was recreated, run `$PY scripts/agent_ctl.py restart --by claude --reason <task>`.
6. The user opens the UI, presses Voice and hears a reply.
7. Add a done_highlight.md entry with the rollback command.

## Adding a JARVIS service

This is for the VPS-brain services such as `agent` and `agent-svc` (planned contract K-75).

1. Record the service in `docs/CONTRACTS.md` before it is deployed.
2. Add it to `docker-compose.yml` with image `jarvis-<name>:hostinger`, container `jarvis-<name>` and a Docker healthcheck. A profile is fine: naming a service on the command line enables its profile.
3. Deploy it by name, for example `--services agent`. No script change is needed:
   - the generic gate waits for the healthcheck;
   - `deploy.sh` makes the `jarvis-<name>:rollback-<backup>` tag;
   - a failed first deploy stops the new container.
4. Optional, in `deploy.sh`: a longer wait in `gate_timeout` (`agent` and `agent-svc` already get 240 s), HTTP probes in `gate_probes`, and any bind-mounted config file in `HOLD_FILES`.
5. `backup_vps.sh` still tags only the bridge and the frontend, and backs up only the bridge volumes. The task that adds the service extends it (its volumes, `agent.env`).
6. Run the full tests below.

## Testing the deploy tooling

```bash
$PY -m pytest tests/unit/test_deploy_tooling.py -q -p no:cacheprovider                       # fast offline set
JARVIS_DEPLOY_E2E=1 $PY -m pytest tests/unit/test_deploy_tooling.py -q -p no:cacheprovider   # plus full runs, about 5 min
```

The tests run the real scripts under Git Bash in a scratch sandbox, with fake docker, nginx, systemctl, curl, ss and ssh. Nothing reaches the VPS or the local Docker. Run the full set after any change to these scripts.

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
- `deploy_from_pc.sh` ships this script with every deploy. `deploy.sh` runs the shipped copy and keeps another in `/root/jarvis-backups/tools/`. C-11 copied it to the VPS once by hand; from C-12 on no manual copy is needed.

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

**1. Images.** This is the fastest way back from a bad deploy. Prefer `rollback.sh` (see [Rollback](#rollback)), which also runs the gate and the smoke check. By hand:

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
