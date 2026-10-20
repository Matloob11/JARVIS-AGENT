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
| [Agent image](#agent-image) | C-126 |
| [Secrets split (V2a, V2b, push)](#secrets-split-v2a-v2b-push), including [the PC copies before V4](#before-v4-pc-copies-of-the-vps-env-files), [the PC fallback secrets](#the-pc-fallback-secrets-vps-mode) and [the PC fallback's LiveKit pair](#v6-the-pc-fallbacks-livekit-pair) | C-127 |
| [Testing the deploy tooling](#testing-the-deploy-tooling) | C-12 (OP-08) |
| [Room ops](#room-ops) | C-23 (OP-22) |
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
   - `$VPS 'D=/root/jarvis-backups/$(cat /root/jarvis-backups/LAST_DEPLOY); diff $D/smoke-pre.txt $D/smoke-post.txt && echo OTHER_APPS_UNCHANGED'` (the deploy's own backup; the newest `2*` folder can be another tool's)
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
$VPS 'bash /root/jarvis-backups/tools/rollback.sh --tag rollback-<backup> --services ui-bridge'              # images only
$VPS 'bash /root/jarvis-backups/tools/rollback.sh --tag rollback-<backup> --services ui-bridge --with-env'   # image + compose + env
$VPS 'bash /root/jarvis-backups/tools/rollback.sh <backup folder> --services ui-bridge --with-env --dry-run'    # env plan + checks
```

- **The default (`--last`)** uses `/root/jarvis-backups/LAST_DEPLOY`. It names the backup that the last deploy took before it changed anything. That backup's `deploy.env` says which services, and whether the code, nginx and env files, go back.
- **A named backup made by deploy.sh or rollback.sh** puts back exactly what that run changed.
- **Any other backup_vps.sh folder** puts back the services whose running image differs from its `containers.txt`. Add `--code`, `--nginx`, `--with-env` or `--services LIST` as needed.
- `--code` restores `/opt/jarvis-agent` from `opt-jarvis-agent.tgz`, but never the env files: a secret rotated after the backup stays rotated.
- `--with-env` (C-127; `--env` is the same) restores the image tags, `docker-compose.yml` and the env files together from one backup: mode 600, the env files that the services of this rollback read (the readers of the layout check below: `.env` from `hostinger.env`, `bridge.env`, `.env.agent` from `env.agent`), plus `root.env`, which no container reads, and, whenever `.env` goes back, the compose file of its `opt-jarvis-agent.tgz` (all of it with `--code`). Every other env file stays as it is: an env file whose readers are not recreated would meet them at their next restart out of step with their image (for example `--services ui-bridge --with-env` never touches `.env.agent`). With no service that reads an env file (`--services frontend`, `--services none`) neither `.env` nor the compose file goes back, so the next recreate of the bridge, LiveKit or the agents never meets the backup's compose file with today's env files. A live `bridge.env` the target never had is moved aside (`bridge.env.pre-rollback-<UTC>`, never deleted), because it would win over the restored `.env`; the live `.env.agent` stays when the backup has none. The live `JARVIS_BRAIN` and `COMPOSE_PROFILES` lines are always written back into the restored `.env`, replacing the backup's: the brain switch owns them (K-72; only `agent_ctl brain` writes `JARVIS_BRAIN`), and a `hostinger.env` from before C-126 has no `JARVIS_BRAIN`, without which the compose file refuses every command. The live Desktop Link key lines are written back the same way: `DESKTOP_AGENT_PUBLIC_KEY` and `DESKTOP_PC_PUBLIC_KEY` into a restored `bridge.env`, those two and `DESKTOP_AGENT_SIGNING_KEY` into a restored `.env.agent` (a live file without one drops the backup's too). Their other halves are on the laptop (its own key, its pin of the agent's key, C-123), which no rollback touches; an older key there would switch the bridge's `/desktop` relay off or make it refuse every PC-signed request, and in vps mode the laptop would go offline. Every other value comes back as the backup holds it, so a value rotated since the backup is undone in the restored files: the owner login of `bridge.env` (`OWNER_PASSWORD_HASH`, `OWNER_TOTP_SECRET`, `OWNER_RECOVERY_HASHES`, `SESSION_JWT_SECRET`: the old password, the old 2FA secret, and every session signed out), and any setting changed since. The shared values are the exception: `AGENT_TOKEN`, `BRIDGE_HMAC_SECRET` (the notify credentials, K-47 v36), `LIVEKIT_API_KEY`/`LIVEKIT_API_SECRET` and the PC fallback's own pair `LIVEKIT_PC_API_KEY`/`LIVEKIT_PC_API_SECRET` (once `livekit_pc_pair.py create` made it, [V6](#v6-the-pc-fallbacks-livekit-pair)) must equal the live ones, because the PC, the laptop and every service the rollback does not recreate keep the live values. `rollback.sh` compares them on the VPS by digest before it changes anything (the dry run too; names only, on its `env values` line) and refuses a difference (exit 1, the names printed): for example a `rotate_env_secret.py --name AGENT_TOKEN` since the backup would come back in `bridge.env` only and the agent's notifies would be dropped, and a backup taken between `livekit_pc_pair.py create` and a `revoke` would bring back the PC secret a lost or compromised laptop holds, which LiveKit accepts again at its next recreate. Make them equal first, or add `--shared-values-differ-ok` when you make them equal again right after the rollback; its printed undo then carries that option too. The plan (and the dry run) lists every file it replaces, moves aside or keeps. With `--tag rollback-<backup>` the backup is that folder.
- **The env layout check (C-127).** `backup_vps.sh` records in `MANIFEST.txt` which env files the containers read exist (`.env`, `bridge.env`, `.env.agent`) and the names each one assigns (`env_layout:` lines, names only). Before it changes anything, `rollback.sh` compares the target's layout (that record; for an older backup the names of its env copies) with the live files, for the files its services read: `ui-bridge` reads `.env` and `bridge.env`, `livekit` reads `.env`, `agent` and `agent-svc` read `.env` and `.env.agent`, and `.env` counts whenever `--code` brings back another compose file. `JARVIS_BRAIN` and `COMPOSE_PROFILES` never count. Like compose and the Python env tools, `backup_vps.sh` and `rollback.sh` read past a UTF-8 byte order mark in front of an env file's first line (a file saved by a Windows editor), so the name on that line counts everywhere: in the layout, in the shared-value digests and in the live `JARVIS_BRAIN`, `COMPOSE_PROFILES` and link key lines a restore keeps. A different layout, for example a target from before the V2b secrets split, is refused (exit 1, nothing changed, the differing names printed) unless `--with-env`: an old image with today's env files, or the reverse, can crash both brains. An unknown layout is refused the same way (exit 1) unless `--layout-unknown-ok`: a `--tag` with no backup folder `rollback-<folder>` (a hand-made tag such as `pre-emergency-...`, or a rollback tag whose folder was pruned) records none, and `--with-env` is refused there (exit 64) because nothing records its env files. Add `--layout-unknown-ok` only once you know those images run with the live env files. The dry run prints the result on its `env layout` line.
- A backup folder and `--tag` are never combined (exit 64): the images of one state with the env layout and env files of another is the mix the check exists to stop. `--tag rollback-<backup>` already takes all three from that backup.
- `--tag T` takes `<image>:T`, for JARVIS's own images (`jarvis-*`) only. The `pre-emergency` images hold the revoked token (see [Known snapshots](#known-snapshots)) and are disaster recovery only. After V2b they cannot run at all: their bridge is from before C-112, its legacy `config.validate()` needs `GOOGLE_API_KEY` from the pre-split `.env`, and it crash-loops with the split env files.
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
5. `backup_vps.sh` still tags only the bridge and the frontend, and backs up only the bridge volumes (and, since C-127, `.env.agent`). The task that adds the service extends it (its volumes; C-128 for the agent services).
6. Run the full tests below.

## Agent image

The VPS brain's image `jarvis-agent:hostinger` (`deploy/hostinger/Dockerfile.agent`, contract K-75) runs both `agent` (container `jarvis-agent`, the LiveKit worker) and `agent-svc` (`jarvis-agent-svc`, the always-on service).

- Both services are in the compose profile `vps-brain`. `docker compose -p jarvis up -d` without it never starts them; naming them (`up -d --no-deps agent`) or `COMPOSE_PROFILES=vps-brain` in `.env` (vps mode, K-72) does.
- `deploy.sh` and `rollback.sh` touch them only when they are named in `--services` or recorded by a deploy that named them, always with `up -d --no-deps --no-build --pull never`.
- They have no `build:` section. The image is built only with the dedicated builder below (D19), never by `deploy.sh` or the default builder, whose cache other apps share. A deploy of `agent` before the image exists fails before any live change.
- Uid 10001, `cap_drop: ALL`, `no-new-privileges`, `oom_score_adj` 500, no published port. No CPU, memory or pids limit and no tmpfs (user decision; `pids_limit` only after a yes to D10).
- `init: false`, so python is PID 1. It makes itself non-dumpable (`harden_process()`, K-72), and no other uid-10001 process (ffmpeg, yt-dlp, node) can read the secrets in `/proc/1/environ`. docker-init would keep them there, dumpable. The value is explicit because without one dockerd applies its own default. The healthchecks run `env -i curl`, so the probe holds no secret either. `agent-svc` (C-125) must handle SIGTERM and call `harden_process()` itself.
- Secrets come only from `deploy/hostinger/.env.agent` (root 600, names in `.env.agent.example`); compose refuses to start them without it.
- `docker-compose.yml` reads `${JARVIS_BRAIN:?}`. Set `JARVIS_BRAIN=pc` in `deploy/hostinger/.env` once, before the first deploy of that compose file, or every compose command (deploy, rollback, the backup fallback) refuses to run. Check the name only: `grep -c '^JARVIS_BRAIN=' /opt/jarvis-agent/deploy/hostinger/.env`.
- `agent-svc` runs `src.core.agent_services` (C-125); do not start it before that module exists.

### Build window (announced; about 10-15 min the first time, then only the changed layers)

First make sure the VPS `.env` has `JARVIS_BRAIN` (see above): `deploy.sh` checks the shipped compose file with the live env files and refuses the run without it. Then, on the PC, ship the commit without touching a container (backup and `DEPLOYED_SHA` included):

```bash
bash deploy/hostinger/deploy_from_pc.sh --services none
```

On the VPS (as root). Downloads need 20 GB free on the Docker disk (K-75); stop below that.

```bash
cd /opt/jarvis-agent
df -h "$(docker info -f '{{.DockerRootDir}}')"
# once (D19): a docker-container builder; no --use, so the default builder stays the default
docker buildx inspect jarvis-builder >/dev/null 2>&1 || docker buildx create --name jarvis-builder --driver docker-container
R=/root/jarvis-backups/builds/$(date -u +%Y%m%dT%H%M%SZ); mkdir -p "$R"
docker buildx du --builder jarvis-builder > "$R/du-before.txt"
docker buildx du | grep -E '^(Shared|Private|Reclaimable|Total):' > "$R/du-default-before.txt"   # summary lines only
( set -o pipefail; docker buildx build --builder jarvis-builder --load --progress=plain \
    -f deploy/hostinger/Dockerfile.agent -t jarvis-agent:hostinger --build-arg GIT_SHA="$(head -n 1 DEPLOYED_SHA)" . \
    2>&1 | tee "$R/build.log" ); echo "BUILD_EXIT=$?"
docker buildx du --builder jarvis-builder > "$R/du-after.txt"
docker buildx du | grep -E '^(Shared|Private|Reclaimable|Total):' > "$R/du-default-after.txt"
diff "$R/du-default-before.txt" "$R/du-default-after.txt" && echo DEFAULT_BUILDER_CACHE_UNCHANGED
```

The build itself already fails on a broken dependency, a GUI OpenCV, a missing ffmpeg/node/yt-dlp, a failed `IMPORT_OK` or a model that does not load offline as uid 10001. The same checks by hand, as uid 10001 and without any network:

```bash
I='docker run --rm --network none --cap-drop ALL --security-opt no-new-privileges jarvis-agent:hostinger'
$I sh -c 'id -u; node --version; ffmpeg -hide_banner -version | sed -n 1p; yt-dlp --version; python -c "import torch; print(torch.__version__)"'
$I env JARVIS_ROLE=agent python -c 'import src.core.agent_runner; print("IMPORT_OK")'
$I python scripts/models/check_models.py --expect-uid 10001          # MODELS_OK
docker image inspect -f '{{.Config.User}} {{index .Config.Labels "org.opencontainers.image.revision"}}' jarvis-agent:hostinger
```

The plan's compose form needs `.env.agent`: compose requires it for these services, and C-127 fills it. Until then an empty root-600 placeholder stands in. The guard never replaces an existing file. The placeholder starts nothing either: a plain `up` leaves the `vps-brain` services out, and agent_ctl's PREFLIGHT still refuses. C-127 replaces it (tmp, then mv). The runs create the empty `jarvis_agent_*` volumes (owner 10001):

```bash
cd /opt/jarvis-agent/deploy/hostinger
[ -e .env.agent ] || install -m 600 -o root -g root /dev/null .env.agent
docker compose -p jarvis run --rm --no-deps agent python -c 'import src.core.agent_runner; print("IMPORT_OK")'
docker compose -p jarvis run --rm --no-deps -e HF_HUB_OFFLINE=1 agent python scripts/models/check_models.py
```

The plan's `docker inspect` acceptance, before anything starts. Compose creates the container without starting it (`--no-start`), from the image already there, and leaves the bridge and LiveKit alone (`--no-deps`). Then `docker rm` removes it; without `-f` it refuses a running container. Run it only while no `jarvis-agent` exists: an `up` would recreate a running one.

```bash
cd /opt/jarvis-agent/deploy/hostinger
if docker container inspect jarvis-agent >/dev/null 2>&1; then
  echo 'jarvis-agent already exists: inspect it as it is, never recreate it here'
else
  docker compose -p jarvis up --no-start --no-deps --no-build --pull never agent
  docker inspect -f 'mem={{.HostConfig.Memory}} cpus={{.HostConfig.NanoCpus}} oom={{.HostConfig.OomScoreAdj}} user={{.Config.User}} init={{.HostConfig.Init}}' jarvis-agent
  docker rm jarvis-agent
fi
```

It prints `mem=0 cpus=0 oom=500 user=10001 init=false`.

Before the first start of either service (and with `.env.agent` in place), give the volumes they share with the bridge to uid 10001, once (K-75). The services drop every capability, so `chown` needs `--cap-add CHOWN`; `agent` mounts the media folders read-write, `agent-svc` the snapshots:

```bash
docker compose -p jarvis run --rm --no-deps --user 0 --cap-add CHOWN --entrypoint chown agent \
  -R 10001:10001 /app/Jarvis_Outputs /app/Jarvis_Shared
docker compose -p jarvis run --rm --no-deps --user 0 --cap-add CHOWN --entrypoint chown agent-svc \
  -R 10001:10001 /app/snapshots
```

After the first `up` (C-128/C-131), the same `docker inspect` line on the running `jarvis-agent` must print the same values. Nothing in `/root/jarvis-backups/builds` or the builder is ever pruned without the user's OK.

## Secrets split (V2a, V2b, push)

Plan C-127, contracts K-47 and K-75. Today the bridge reads `.env` (the compose interpolation values plus the AI, search, email and SIM keys, `JARVIS_ENCRYPTION_KEY` and `CONTROLLER_TOKEN`) and `bridge.env` (the agent link and login secrets, which win). After the split:

| File (VPS, root 600) | Holds | Read by |
|---|---|---|
| `.env` | every variable `docker-compose.yml` interpolates (`JARVIS_BRAIN`, the LiveKit pair, the legacy token, later the PC pair), `COMPOSE_PROFILES`, `JARVIS_PC_AGENT_NAME` and the other non-secret names it has today; no agent secret | compose; ui-bridge until C-18 drops it from its `env_file` |
| `bridge.env` | its K-47 names, the Desktop Link public keys, and the bridge settings that only `.env` held so far (`LOG_LEVEL`, `LOG_FILE` ...) | ui-bridge (it wins over `.env`) |
| `.env.agent` | the K-47 v4 names from the PC root `.env`, `AGENT_TOKEN`, `BRIDGE_HMAC_SECRET`, the main LiveKit pair, the agent link key | agent, agent-svc |

The tools run on the PC and send one program over ssh stdin (python3 on the VPS, standard library only); they print names, lengths, file modes and public keys, never a value. After the split, change a bridge setting in `bridge.env`, not `.env`: `bridge.env` wins.

Three stages, each in its own session. The PC brain keeps answering through all of them.

### V2a: the bridge with the role-aware validate (env files unchanged)

The bridge in HEAD calls `config.validate('bridge')` (C-112): it needs the LiveKit pair and, while `LEGACY_TOKEN_AUTH` is on, the legacy token, never an AI key. V2a ships that code while both env files stay as they are.

```bash
$PY scripts/ops/backup_local.py --now                                    # 1. PC backup
bash deploy/hostinger/deploy_from_pc.sh --dry-run --services ui-bridge   # 2. offline plan
bash deploy/hostinger/deploy_from_pc.sh --check --services ui-bridge     # 3. dry run on the VPS
bash deploy/hostinger/deploy_from_pc.sh --services ui-bridge             # 4. backup (it records the env layout), deploy, gate
$VPS 'D=$(cat /root/jarvis-backups/LAST_DEPLOY); grep "^env_layout:" /root/jarvis-backups/$D/MANIFEST.txt'   # 5. names only
```

6. The user asks one voice question and sends one typed message; the PC brain answers both.
7. The rollback drill, back to the previous bridge image and forward again. The env files did not change, so the dry run prints `env layout   same as ...`:

```bash
$VPS 'bash /root/jarvis-backups/tools/rollback.sh --dry-run'
$VPS 'bash /root/jarvis-backups/tools/rollback.sh'                  # healthy on the previous bridge image
$VPS 'bash /root/jarvis-backups/tools/rollback.sh <the backup it printed>'   # forward again, healthy
```

Before V2b, `$PY scripts/ops/split_bridge_env.py --check-bridge` still lists the agent secret names in `jarvis-bridge` (exit 1): that is the state V2b fixes.

### V2b: the env split (env files only)

```bash
$PY scripts/ops/backup_local.py --now                                                    # 1. PC backup
$VPS 'bash /opt/jarvis-agent/deploy/hostinger/backup_vps.sh pre-env-split | tail -n 1'    # 2. BACKUP_DIR=<folder>: note it
$VPS 'grep "^env_layout:" <folder>/MANIFEST.txt; ls <folder>'                            # 3. names only; hostinger.env, bridge.env there
$PY scripts/ops/split_bridge_env.py --dry-run                                            # 4. the plan, names only
$PY scripts/ops/split_bridge_env.py                                                      # 5. its own backup, then bridge.env and .env
$VPS 'stat -c "%a %U %n" /opt/jarvis-agent/deploy/hostinger/.env /opt/jarvis-agent/deploy/hostinger/bridge.env'   # 6. 600 root
```

- Step 4 must show `removes` the agent secret names only, `docker-compose.yml interpolates ...` with every name kept in `.env`, and no `REFUSED:` line. Read the warnings: a name that looks like a secret but is on no list stays in `.env` until you decide. A refusal writes nothing; fix its cause and run the dry run again.
- Step 5 also copies `.env` and `bridge.env` to `/root/jarvis-backups/env/<UTC>-pre-env-split/` (folder 700, files 600) before it writes; each file goes through a temp file and a rename. The env tools (`split_bridge_env.py`, `push_agent_env.py`, `livekit_pc_pair.py`) keep their backups in `/root/jarvis-backups/env/`, never in a `2*` folder beside `backup_vps.sh`'s, so the PC mirror and the "newest backup" commands below never take one of them.

7. Recreate the bridge alone, from its image and with its own `GIT_SHA` (only its env files changed), then the gate:

```bash
$VPS 'cd /opt/jarvis-agent/deploy/hostinger && sha=$(docker inspect -f "{{range .Config.Env}}{{println .}}{{end}}" jarvis-bridge | sed -n "s/^GIT_SHA=//p" | head -n 1) && GIT_SHA="$sha" docker compose -p jarvis up -d --no-deps --no-build --pull never --force-recreate ui-bridge'
$VPS 'bash /root/jarvis-backups/tools/deploy.sh --gate-only --services ui-bridge'    # GATE=ok
```

8. Step 7 of the plan, names only (`docker inspect` is cut to names on the VPS):

```bash
$PY scripts/ops/split_bridge_env.py --check-bridge                     # NO_AGENT_SECRETS_IN_BRIDGE, exit 0
$VPS "docker exec jarvis-bridge env | cut -d= -f1" | $PY scripts/ops/split_bridge_env.py --check-names -   # the same check, by hand
```

9. The user asks one voice question and sends one typed message; the PC brain answers both.
10. The done_highlight.md entry names the undo: `$VPS 'bash /root/jarvis-backups/tools/rollback.sh <folder of step 2> --services ui-bridge --with-env'` (dry run first: it lists each file it replaces or keeps, and its `env values` line). It puts back the pre-split `.env` and `bridge.env` as step 2 saved them and recreates the bridge from the same image. `.env.agent` stays as it is (no service of that rollback reads it), the live `JARVIS_BRAIN` and `COMPOSE_PROFILES` lines are written back into the restored `.env`, and the live Desktop Link public keys (`push_agent_env.py --keys` adds them after step 2) into the restored `bridge.env`, so the laptop stays paired. Every other value changed since step 2 comes back as step 2 saved it, for example the owner login (`set_owner_credentials.py`) and `SESSION_JWT_SECRET` (every session signed out). It refuses (exit 1, names only) while `AGENT_TOKEN`, `BRIDGE_HMAC_SECRET`, the LiveKit pair or the PC fallback's pair (after V6) differ from the live values (rotated or revoked since step 2): the agent's notifies would be dropped, LiveKit would refuse the bridge, or a revoked PC pair would work again. Make them equal first, or add `--shared-values-differ-ok` and make them equal again right after.

A later rollback to any state from before the split needs `--with-env` ([Rollback](#rollback)): the dry run refuses without it and names the env names that differ.

### Push the agent env (after V2b, before V4)

```bash
$PY scripts/ops/backup_local.py --now
$VPS 'bash /opt/jarvis-agent/deploy/hostinger/backup_vps.sh pre-agent-env | tail -n 1'
$PY scripts/ops/push_agent_env.py --dry-run           # names: add / replace / keep (equal), no value sent
$PY scripts/ops/push_agent_env.py                     # writes .env.agent (backup first), then compares by HMAC digest
```

- The push copies `PC_NAMES` from the PC root `.env` (K-47's single source) and the main LiveKit pair from the VPS `.env`, which never leaves the VPS. Every comparison line must say `equal`. `AGENT_TOKEN` and `BRIDGE_HMAC_SECRET` must equal `bridge.env`, or the agent's notifies are dropped: the VPS step compares them before it writes and refuses (nothing written) when they differ or `bridge.env` lacks them.
- It refuses before sending anything while `GOOGLE_API_KEY`, `JARVIS_ENCRYPTION_KEY`, `AGENT_TOKEN` or `BRIDGE_HMAC_SECRET` is missing on the PC, or while pushed names are DPAPI-wrapped (vps mode): push before `agent_ctl brain vps`.

The agent's Desktop Link key, made on the VPS in a one-off agent container (`--network none`, the folder of `.env.agent` mounted; the C-115 As-built form). First the laptop's own key, as the user who runs the node, if `DESKTOP_PC_PUBLIC_KEY` is not in the root `.env` yet:

```bash
$PY scripts/ops/gen_desktop_keys.py --role pc         # prints DESKTOP_PC_PUBLIC_KEY=<hex>, a public key
$PY scripts/ops/env_set.py DESKTOP_PC_PUBLIC_KEY=<that hex> --env-file .env
$PY scripts/ops/push_agent_env.py --keys --dry-run    # the docker run line, nothing run
$PY scripts/ops/push_agent_env.py --keys              # public keys only; bridge.env and the PC root .env get them
$VPS 'stat -c "%a %U %n" /opt/jarvis-agent/deploy/hostinger/.env.agent /opt/jarvis-agent/deploy/hostinger/bridge.env'   # 600 root
```

Then recreate the bridge (it reads the public keys from `bridge.env`) as in V2b step 7, run `split_bridge_env.py --check-bridge` again, and end with `$PY scripts/ops/push_agent_env.py --check` (all `equal`). `agent_ctl node update` pins the agent public key on the laptop at V4 (C-123). An existing agent key is kept; `--keys --rotate` replaces it (backups first, as above).

### Before V4: PC copies of the VPS env files

`fallback_env.py --wrap`, and so the wrap of `agent_ctl brain vps`, refuses while a PC copy of a VPS env file inside the repo holds a PC fallback secret in plain text (or cannot be read): `deploy/hostinger/.env` (for example a stale copy of the pre-split VPS `.env`), `deploy/hostinger/bridge.env` or `deploy/hostinger/.env.agent`, the git-ignored files the pre-commit hook scans too. No JARVIS process reads them and no deploy ships them (env files are never shipped), but in vps mode the Desktop Node and every process it starts would find those secrets on disk. Before V4, on the PC in Git Bash from the repo root, move them out of the repo after a backup outside it:

```bash
$PY scripts/ops/fallback_env.py --status       # names only: one '(PC copy of a VPS env file)' line per copy
$PY scripts/ops/backup_local.py --now          # the usual PC backup first (its zip does not hold these copies)
D="$PCB/pc-env-copies/$(date -u +%Y%m%dT%H%M%SZ)" && mkdir -p "$D"
for f in deploy/hostinger/.env deploy/hostinger/bridge.env deploy/hostinger/.env.agent; do
  [ -f "$f" ] || continue
  t="$D/$(printf '%s' "$f" | tr / _)"
  cp -p "$f" "$t" && cmp -s "$f" "$t" && rm "$f" && echo "moved $f"   # names only; never print a copy
done
$PY scripts/ops/fallback_env.py --status       # no '(PC copy of a VPS env file)' line is left
```

- `$PCB` is the PC backup folder outside the repo (the shorthands at the top). A copy leaves the repo only once its backup there reads back identical. Keep that folder as long as you may need the copies, and never copy one back into the repo: they hold the values of their day, while the live values are on the VPS and in the PC root `.env` (or the store).
- Until then `--status` names each copy with the fallback names it holds, and `--wrap` refuses with the same names (exit 1, nothing written).

### The PC fallback secrets (vps mode)

User decision D13: while the VPS brain runs, the PC fallback's secrets (`LIVEKIT_API_SECRET`, the legacy token, the AI, image, search and weather keys, `SIM_LOOKUP_API_KEY`, `EMAIL_APP_PASSWORD`) leave the PC env files for the DPAPI-wrapped `run/fallback.env.dpapi` (`scripts/ops/fallback_env.py`, the node keys' DPAPI helper, a file only this Windows user can open). Neither the Desktop Node nor a process it starts finds them in the root `.env` then.

- `agent_ctl brain vps` wraps them once the node has started. `agent_ctl brain pc` unwraps them back into the env files before it starts the PC worker; a `brain pc` that starts nothing (already pc, or `--laptop-only`) does it at its end. Never while the Desktop Node still runs: a `brain pc` that would start nothing exits 4, and the PC worker start of a switch (or of the restore of a failed `brain vps` whose node stop failed) is not made, so the secrets stay wrapped (stop the node, then run `brain pc` again). Nothing else unwraps them, and a PC worker start is refused (exit 4) while the store exists.
- Names only: `$PY scripts/ops/fallback_env.py --status`. By hand: `--wrap [--dry-run]`, and `--restore [--dry-run]` as the emergency way back. `--wrap` refuses while a PC copy of a VPS env file holds a fallback secret ([Before V4](#before-v4-pc-copies-of-the-vps-env-files)).
- A store this Windows user can never unwrap (the repo copied to another PC or Windows user, a rebuilt Windows profile) keeps the PC fallback down: every PC worker start is refused while it exists, `brain pc` cannot restore it, and the tools that read it refuse (`could not be unwrapped by this Windows user`, followed by this way out). Move it aside, never delete it, then enter the values again:

  ```bash
  $PY scripts/ops/fallback_env.py --discard --dry-run   # names only: why it cannot be used, what it holds, where it would go
  $PY scripts/ops/fallback_env.py --discard --confirm   # renamed to run/fallback.env.dpapi.unreadable-<UTC>
  # once per name it held: n the name on this PC, v the name in the VPS copy, f that copy (.env.agent: the AI, image,
  # search, weather, SIM and email keys; .env: LIVEKIT_API_SECRET, LIVEKIT_PC_API_SECRET and the legacy token)
  n=GOOGLE_API_KEY v=GOOGLE_API_KEY f=.env.agent
  $VPS "sed -n 's/^$v=//p' /opt/jarvis-agent/deploy/hostinger/$f | tail -n 1" | sed "s/^'\(.*\)'\$/\1/" | $PY scripts/ops/fallback_env.py --set $n --value-from-stdin
  $PY scripts/ops/push_agent_env.py --check             # each pushed name: 'equal to this PC'
  $PY scripts/ops/fallback_env.py --wrap                # vps mode: wrapped again, for this Windows user
  ```

  `--discard` refuses while this Windows user can unwrap the store (`--restore` or `brain pc` then puts the values back), and it never overwrites an earlier aside file; delete that file only once you are sure (renamed back, it is the store again). Each pipe carries one value from the VPS straight into `--set` (no store now, so into the root `.env`), never onto a screen or a command line; the second `sed` drops the single quotes the env tools put around a value with spaces. After V6 `activate` this PC's `LIVEKIT_API_SECRET` is the PC pair's: `n=LIVEKIT_API_SECRET v=LIVEKIT_PC_API_SECRET f=.env`, then `livekit_pc_pair.py verify`. Instead of copying, a name can get a new value on both sides with `rotate_env_secret.py` (the main LiveKit pair: `rotate_livekit_keys.py`; the PC pair: `livekit_pc_pair.py create --rotate`, the LiveKit recreate and `activate`). In vps mode run `--wrap` right after the last `--set`, so the values do not stay in the root `.env` beside the Desktop Node.
- In vps mode a rotated value goes where its name is now, and `rotate_env_secret.py` does both sides in one run: `$PY scripts/ops/rotate_env_secret.py --name NAME --pc-files .env --vps-file /opt/jarvis-agent/deploy/hostinger/.env.agent [--value-from-stdin]`. While `run/fallback.env.dpapi` exists, a fallback name (`fallback_env.FALLBACK_NAMES`) given with `--pc-files` goes into that store (`fallback_env.put_values`), never into an env file, and the given env files lose any copy of it; `--alias` follows when the PC holds it. Before its VPS step the tool checks, writing nothing, that this Windows user can unwrap the store and that the env files can be written, and refuses otherwise (exit 2, nothing written anywhere). A `--name` and `--alias` of which only one is a fallback name are refused (exit 2): rotate them one at a time. The PC side alone: `$PY scripts/ops/fallback_env.py --set NAME --value-from-stdin`. Either way the value is piped in or typed into a console that hides it, never on a command line (C-18).
- The pre-commit hook still finds a wrapped value in a staged line (`ENV_VALUE:<NAME>`): it unwraps the store in its own process, as this Windows user, and keeps the values in memory only. When it cannot (another Windows user, a damaged store), it names the wrapped names on stderr, with the way out above, and runs every other check.
- The LiveKit pair in vps mode (backups first, an announced window): `$PY scripts/ops/rotate_livekit_keys.py --dry-run`, then without `--dry-run`, with the default PC files. While `run/fallback.env.dpapi` exists the new `LIVEKIT_API_SECRET` goes into that store, never into an env file, and `LIVEKIT_API_KEY` into the root `.env` (`deploy/windows-local/.env` loses its own key line, so no old copy stays); before its VPS step the tool checks, writing nothing, that this Windows user can unwrap the store and the env files can be written, and refuses otherwise (exit 2, nothing written anywhere). If the PC side still fails after the VPS step (exit 1, names printed), fix the cause and run the rotation again: it makes a new pair for both sides (between V6 `create` and `activate` that run is refused, because the PC's key is no longer the VPS one: run `activate`, or copy the VPS `.env`'s main pair over with `fallback_env.py --set`). Then on the VPS recreate `livekit` and `ui-bridge` and the VPS brain's `agent` and `agent-svc` (they read the pair from `.env.agent`): the two `docker compose` lines the tool prints. Then `--check-old <backup it printed>` (`old key rejected: 401`). The PC worker takes the new pair at the next `agent_ctl brain pc`, which unwraps the store. After V6 `activate` the PC holds the PC pair instead (after a later `create --rotate` still the earlier one, until `activate` runs again): rotate the main pair with `--pc-files ''`. A rotation with PC files is then refused by its VPS step (exit 2, nothing written on the VPS or the PC, the dry run too): the tool sends the PC's `LIVEKIT_API_KEY` values (keys, never a secret) along, and while the VPS `.env` holds a PC pair the VPS step refuses when one of them is its `LIVEKIT_PC_API_KEY`, or when none of them is its `LIVEKIT_API_KEY` (the PC does not hold the main pair), because the new main pair would overwrite the PC pair or reach a PC that does not hold it, and a later `revoke` would cut nothing off. Before V6 `create` neither check applies, so a PC that an earlier rotation missed is brought up to date.
- `backup_local.py` copies the env files, not `run/`: in vps mode its zip holds no fallback secret. The store opens only for this Windows user on this PC; `.env.agent` (AI keys) and the VPS env files (LiveKit, legacy token) hold the same values.

### V6: the PC fallback's LiveKit pair

Plan step 6: the PC fallback gets its own LiveKit pair, so it can be revoked without touching the bridge, the VPS agent or the browsers. It is applied only in the V6 LiveKit window (announced; LiveKit restarts once). The tool support is `scripts/ops/livekit_pc_pair.py`; until that window `docker-compose.yml` names one pair only.

1. `$PY scripts/ops/livekit_pc_pair.py create --dry-run`, then `create`: the VPS `.env` gets `LIVEKIT_PC_API_KEY` and `LIVEKIT_PC_API_SECRET` (backup first), and the PC the same pending pair (the secret in the DPAPI store while wrapped). Nothing uses it yet.
2. In the window, the compose change, committed and deployed with `--services livekit` (RUNBOOK [Deploy](#deploy-claude-only)); the `\n` in the double-quoted string makes the two lines LiveKit parses as two keys, and `:?` makes compose refuse while the pair is missing:

   ```yaml
       environment:
         LIVEKIT_KEYS: "${LIVEKIT_API_KEY}: ${LIVEKIT_API_SECRET}\n${LIVEKIT_PC_API_KEY:?run livekit_pc_pair.py create}: ${LIVEKIT_PC_API_SECRET:?run livekit_pc_pair.py create}"
   ```

   `split_bridge_env.py` keeps both names in `.env` from then on (they are interpolated). Restart the PC worker after the LiveKit recreate if the PC brain runs (`agent_ctl restart`), as after every LiveKit restart, and check one voice question: the bridge, the VPS agent and the browsers still use the main pair.
3. `$PY scripts/ops/livekit_pc_pair.py verify`: `PC pair accepted`, then `this PC's ... are the VPS .env pair (HMAC digests)`. It checks this PC's own copy, the one the PC worker would use (the pending names before `activate`, `LIVEKIT_API_KEY`/`LIVEKIT_API_SECRET` after it; a wrapped secret is unwrapped in the tool's process): that copy goes on ssh stdin to the VPS, a ListRooms call signed with it is made inside `jarvis-bridge`, and the VPS sends back only the HMAC digests of its own pair to compare. Exit 0 only when LiveKit accepts this PC's copy and it equals the VPS `.env` pair.
4. `activate --dry-run`, then `activate`: verify again with the pending pair, which LiveKit must accept and whose key and secret must both equal the VPS ones by HMAC digest (a revoked or stale pending secret is refused, so it never replaces the working main pair), then the PC's `LIVEKIT_API_KEY`/`LIVEKIT_API_SECRET` take the PC pair (the key in the root `.env`, the secret in the root `.env` or, while wrapped, in the DPAPI store), and `deploy/windows-local/.env` loses its own `LIVEKIT_API_KEY`/`LIVEKIT_API_SECRET` lines, so no copy of the main pair stays on the PC. The env files are staged before the store is written; when they cannot be written (for example a read-only `.env`, or one that another program holds open), the store is put back byte for byte and `activate` can simply run again. The PC worker uses the PC pair from its next start. From then on rotate the main pair with `rotate_livekit_keys.py --pc-files ''`: the PC no longer holds it, and a rotation with PC files is refused (exit 2, nothing written), since it would overwrite the PC pair or hand the PC the main pair (also after a later `create --rotate`, until `activate` runs again).
5. Revoke the PC pair alone: `revoke --dry-run`, then `revoke` (the VPS secret gets a random value nobody holds). Until LiveKit is recreated it still accepts the old secret: `verify` prints `PC pair accepted` and that this PC's secret is no longer the VPS one, exit 1. Then in an announced window `docker compose -p jarvis up -d --no-deps --force-recreate livekit`; `verify` then prints `PC pair rejected: 401`, exit 1. Back: `create --rotate`, the LiveKit recreate, `verify`, `activate`. The PC pair is one of the shared values of `rollback.sh --with-env` ([Rollback](#rollback)): a rollback to a backup taken between `create` and the revoke is refused (exit 1, names only), because its `.env` holds the revoked secret (a backup from before `create` holds no PC pair at all); `--shared-values-differ-ok` only with a `revoke` again right after.

## Testing the deploy tooling

```bash
$PY -m pytest tests/unit/test_deploy_tooling.py -q -p no:cacheprovider                       # fast offline set
JARVIS_DEPLOY_E2E=1 $PY -m pytest tests/unit/test_deploy_tooling.py -q -p no:cacheprovider   # plus full runs, about 5 min
```

The tests run the real scripts under Git Bash in a scratch sandbox, with fake docker, nginx, systemctl, curl, ss and ssh. Nothing reaches the VPS or the local Docker. Run the full set after any change to these scripts.

## Room ops

Since C-23 (contract K-44 v9) the bridge (`jarvis-bridge`, module `src/core/bridge_rooms.py`) owns the LiveKit room `jarvis-hybrid`. It creates the room when it is missing, dispatches the agent, and removes a surplus agent. It never deletes the room (only `reset-room` below does) and never deletes a dispatch.

- The PC's `deploy/windows-local/ensure_room.py` (run by agent_ctl and by the room watchdog) asks the bridge first. It uses its own LiveKit path only when the bridge provably does not reconcile: no connection, HTTP 404, nginx's 502/503/504 page, HTTP 503 `livekit_unavailable`, or result `observe`/`disabled`. A bridge that says `paused` stops it in every mode.
- Modes: `always` (keep one agent in the room; the default with the PC brain), `on_demand` (only while a person, a UI or a voice request is there; the default with the VPS brain), `observe` (report only: no loop dispatch, no removal, nothing sent to an agent; it keeps the old token route's heal without its delete: `GET /livekit/token` creates a missing room, and the owner's own Voice connect, Wake or `unpause` adds one dispatch to a room without an agent). The first C-23 deploy runs `observe` from the compose file.
- Pause is held only in `always` and `on_demand`. In `observe` (and on the PC's local bridge) the UI's Pause is refused (`pause_unavailable`) and changes nothing; the admin CLI's `pause` is kept and stops every new dispatch, the PC's ensure_room.py included, and reaches the agent only when the mode leaves `observe`.
- Logs: `ROOM ...` lines in `docker logs jarvis-bridge`. They contain no identity, key or worker id.

```bash
$VPS "docker exec jarvis-bridge python -m src.core.bridge_rooms status"         # mode and its source, paused, PC agent name, last dispatch, one live read
$PY scripts/smoke/room_check.py status                                           # the same from the PC: agent + livekit blocks of /vortex/status
$VPS "docker exec jarvis-bridge python -m src.core.bridge_rooms mode always"     # or on_demand | observe | clear (back to the env/default)
$VPS "docker exec jarvis-bridge python -m src.core.bridge_rooms pause"           # or unpause (Wake)
$VPS "docker logs --since 15m jarvis-bridge 2>&1 | grep -E 'ROOM |Traceback'"
$VPS "docker logs --since 24h jarvis-livekit 2>&1 | grep -c 'RoomService.DeleteRoom'"   # 0 after C-23
```

- `mode`, `pause` and `unpause` write the bridge's kv. The running bridge applies them within 5 s, with no deploy. `mode observe` is the instant kill switch: it stops every loop dispatch and every removal (only the owner's own Voice connect, Wake or `unpause` can still add one dispatch to a room without an agent).
- Before `mode always` (or `on_demand`): the UI's Reconnect must no longer send `start` while JARVIS is paused (Cursor request in `plan.md` section 11, C-23 item 1). Until that UI is live, a Reconnect wakes a JARVIS the owner paused, so keep `observe` or tell the owner.
- A kv mode and the paused flag survive a rollback and a redeploy (`bridge.db` is not rolled back), and a kv mode wins over the compose `AGENT_DISPATCH_MODE` (the bridge logs a WARNING at start). After `rollback.sh`, and before a redeploy that should start from the compose mode, run `mode clear`, and `unpause` unless the owner wants the pause kept.
- `reset-room --confirm jarvis-hybrid` deletes the room and cuts every session in it. Run it only with the owner's OK. It writes the audit row `room_reset`, and the bridge creates the room again on its next pass.

Pause / Wake / Stop probe, only in `always` or `on_demand` mode (in `observe` the bridge refuses `pause_agent` and room_check exits 1 with `pause_unavailable`). JARVIS is silent for about a minute. Never echo the cookie:

```bash
export C23_COOKIE="$($VPS 'docker exec jarvis-bridge python -m src.core.bridge_auth mint-session --ttl 600 --label c23-probe')"
$PY scripts/smoke/room_check.py command pause_agent --wait absent --timeout 40    # paused, the agent leaves ('paused_by_owner' in logs/agent_stderr.log)
$PY scripts/smoke/room_check.py command start --wait voice_ready --timeout 45      # back within 30 s
$PY scripts/smoke/room_check.py command stop --watch 20                            # only interrupts: voice_ready holds, no session restart
unset C23_COOKIE
```

### No agent after a worker restart: LiveKit ghost workers (seen 2026-10-08)

Symptom: `agent_ctl restart` ends with `WARNING NO AGENT in the room: the bridge reports in_flight`, `agent_ctl status` shows the LiveKit link `connected` but `jobs 0 active`, and the bridge keeps logging `ROOM dispatched '' ... ROOM in_flight`. Cause: earlier PC worker connections that died silently (laptop sleep, network flaps: `LIVEKIT LINK disconnected ... worker connection closed unexpectedly` in `logs/agent_stderr.log`) are still registered in LiveKit; it hands every dispatch to one of them and the assignment times out, so the live worker never gets a job.

```bash
$VPS "docker logs --since 10m jarvis-livekit 2>&1 | grep -E 'worker registered|failed to assign job|assigned job'"
# 'failed to assign job to worker ... context deadline exceeded' for worker IDs other than the one agent_ctl status shows = ghosts
$VPS "docker restart jarvis-livekit"      # project jarvis only; drops every LiveKit connection, so only while nobody talks
$PY scripts/agent_ctl.py status           # the worker re-registers by itself; 'jobs 1 active' within ~15 s
```

The restart wipes the in-memory room; the bridge creates it again and dispatches. On 08 Oct the job was back 10 s after the restart. A lasting fix (a per-run agent name the bridge targets, or a server-side worker timeout) is a C-136/C-23 follow-up (plan.md section 11, FINDING 08 Oct 17:10).

### LiveKit webhooks (C-23 phase 4, optional)

Webhooks only wake the bridge's loop: the 5 s / 20 s loop alone keeps the room right. They need a LiveKit restart. That restart drops every call and wipes every room, so do it only in an announced idle window. Commit both parts together in phase 4. Neither is in the tree before then.

1. Append to `deploy/hostinger/livekit.yaml` (the key comes only from the environment, never from this file):

   ```yaml
   webhook:
     urls:
       - http://jarvis-bridge:5002/internal/livekit/webhook
     filter_params:
       include_events: [room_started, room_finished, participant_joined, participant_left]
   ```

2. Add `LIVEKIT_WEBHOOK_API_KEY: "${LIVEKIT_API_KEY}"` to the `livekit` service's `environment` in `docker-compose.yml`. LiveKit v1.13.7 does not start when `webhook.urls` is set and that key is not one of `LIVEKIT_KEYS`.
3. `bash deploy/hostinger/deploy_from_pc.sh --check --services livekit --allow-dirty`, then the same without `--check`. The gate needs LiveKit to answer 200 and `/rtc/validate` to answer 401 within 60 s.

Check:

```bash
$VPS "docker logs --since 5m jarvis-livekit 2>&1 | grep -ci 'api_key is required'"   # 0
$VPS "docker logs --since 5m jarvis-bridge 2>&1 | grep 'ROOM webhook'"              # room_started, participant_joined ...
# The webhook route is /internal/livekit/webhook on :5002 only; it must answer 404 from everywhere else:
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://jarvis.srv1802669.hstgr.cloud/vortex/internal/livekit/webhook   # 404 (nginx)
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://jarvis.srv1802669.hstgr.cloud/vortex/livekit/webhook            # 404 (nginx)
$VPS "curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:5101/internal/livekit/webhook"                 # 404 (InternalOnly)
```

Rollback: `$VPS /root/jarvis-backups/tools/rollback.sh --last`. That is one more short LiveKit restart.

## Backup & Restore

### What gets backed up

**VPS:** `deploy/hostinger/backup_vps.sh [label]` writes `/root/jarvis-backups/<YYYY-MM-DD_HHMMZ>[-label]/`. The time is UTC. The folder is mode 700 and every file in it is mode 600.

| File | Source | Notes |
|---|---|---|
| `opt-jarvis-agent.tgz` | `/opt/jarvis-agent` without `stonix_ui/node_modules` | Members are `jarvis-agent/...`, so extract with `-C /opt`. Also holds `livekit.yaml` and the env files except `.env.agent` (C-127: the agent's secrets are only in `env.agent`, which the PC mirror leaves out). |
| `jarvis.conf` | `/etc/nginx/conf.d/jarvis.conf` | |
| `jarvis.htpasswd` | `/etc/nginx/jarvis.htpasswd` | Copied only when present. |
| `hostinger.env` | `/opt/jarvis-agent/deploy/hostinger/.env` | Compose interpolation and the bridge `env_file`. |
| `bridge.env` | `/opt/jarvis-agent/deploy/hostinger/bridge.env` | Copied only when present (after C-ENV-1). |
| `env.agent` | `/opt/jarvis-agent/deploy/hostinger/.env.agent` | Copied only when present (C-127; the VPS brain's secrets, the agent's link private key among them, K-70). It stays on the VPS: the PC mirror below leaves it out. |
| `root.env` | `/opt/jarvis-agent/.env` | Copied only when present. |
| `DEPLOYED_SHA` | `/opt/jarvis-agent/DEPLOYED_SHA` | Copied only when present (after C-12). |
| `volumes.tgz` | `/app/logs`, `/app/Jarvis_Outputs`, `/app/Jarvis_Shared`, `/app/data` of the bridge | Made by `docker exec jarvis-bridge tar`. If the bridge is down, a one-off `compose run` container makes it instead. The live SQLite files are left out when `bridge.db` was captured. |
| `bridge.db` | `/app/data/bridge.db` | Present only when that DB exists. Taken online with the SQLite backup API, passes `integrity_check`, then `docker cp`. |
| `images.txt` | Docker | Rollback tags with their image ids, plus the LiveKit image ref and id. |
| `docker-ps.txt`, `containers.txt` | Docker | State of project `jarvis`: image, health and mounts. No env values. |
| `MANIFEST.txt`, `SHA256SUMS` | | Summary with any warnings and errors, the env layout (`env_layout:` lines: each env file the containers read, present or absent, and its names, never a value; [Rollback](#rollback) compares it), plus checksums for `sha256sum -c`. |

The script also tags the images that the `jarvis-bridge` and `jarvis-frontend` containers are running as `jarvis-bridge:rollback-<folder>` and `jarvis-frontend:rollback-<folder>`. These are normally the `:hostinger` images.

The script never deletes or changes an older backup. It prints `BACKUP_DIR=<folder>` as its last line, and it exits with 1 if any step failed. A failed run still keeps its partial folder.

**PC** (`scripts/ops/backup_local.py`, folder `%USERPROFILE%\JARVIS-backups\local` or `JARVIS_BACKUP_DIR`):

- `--now` writes `backup_<YYYYmmdd_HHMMSS>.zip`. It holds `.env`, `deploy/windows-local/.env`, `conversations/`, `chroma_db/`, `data/identity/`, `Jarvis_Outputs/`, `backups/checkpoints/`, `logs/*.log`, `logs/telemetry.jsonl`, `plan.md` and `done_highlight.md`, with paths relative to the repo root. SQLite files are copied with the online backup API. Rotation keeps the newest 30 `backup_<ts>.zip` files and never touches any other file.
- `--full-tree` writes `tree_<ts>.tar.gz`: the whole repo, including `.git`, without `.venv_312`, `stonix_ui/node_modules`, `pretrained_models` and caches. Add `--no-git` to also leave out `.git`. Rotation never deletes these archives.
- `--list` shows the local backups, the older one-off archives in `JARVIS-backups\` and the VPS mirrors in `JARVIS-backups\vps\`.

The VPS mirrors and the PC backups contain secrets (env files, htpasswd, tokens inside the tarballs). Keep them in `JARVIS-backups`, which is outside the repo, and never commit or share them. The mirror never holds `.env.agent` (C-127): K-70 keeps the agent's link private key on the VPS, and in vps mode the PC holds its AI keys only DPAPI-wrapped (D13). Encrypting the other env copies to the PC backup key is C-128.

### Known snapshots

| Snapshot | Made by | Image tags | Note |
|---|---|---|---|
| `2026-09-30_2340` | Manual emergency snapshot, taken before the token rotation | `jarvis-{bridge,frontend}:pre-emergency-2026-09-30_2340` | Different layout: `opt-jarvis-agent.tgz`, `nginx-jarvis.conf(.live)`, `docker-inspect.json` (contains env values), `bridge-logs/`, `bridge-Jarvis_Outputs/`, `bridge-Jarvis_Shared/`. Disaster recovery only (see below). |
| `2026-10-01_0825Z-first-scripted` | `backup_vps.sh` v1, first run | `jarvis-{bridge,frontend}:rollback-2026-10-01_0825Z-first-scripted` | State after the emergency fixes. |
| `2026-10-01_0826Z-verify` | `backup_vps.sh` v1, verify run | `jarvis-{bridge,frontend}:rollback-2026-10-01_0826Z-verify` | Same state as above. |

Treat the `pre-emergency` snapshot as disaster recovery only. Its code archive has the old, revoked token and `5100` published on all interfaces, and its frontend image has the revoked token in the JS bundle. Restoring it undoes the 30 Sep security fixes, so use it only to recover data or as a last resort. After V2b (C-127) its bridge image cannot start with the split env files at all (its legacy `config.validate()` needs `GOOGLE_API_KEY` in `.env`), and `rollback.sh` refuses its tags, which record no env layout, unless `--layout-unknown-ok`.

### Take a VPS backup

```bash
$VPS 'bash /opt/jarvis-agent/deploy/hostinger/backup_vps.sh pre-deploy'
```

- The label is optional: 1-40 characters from `A-Z a-z 0-9 . _ -`. Two runs in the same minute get a `-2`, `-3`, ... suffix.
- `deploy_from_pc.sh` ships this script with every deploy. `deploy.sh` runs the shipped copy and keeps another in `/root/jarvis-backups/tools/`. C-11 copied it to the VPS once by hand; from C-12 on no manual copy is needed.

### Mirror VPS backups to the PC

The mirror takes only `backup_vps.sh` folders (they hold `MANIFEST.txt`; the env tools' backups in `/root/jarvis-backups/env/` and other tools' folders are never copied) and streams each one through tar without its `env.agent` (C-127), so that file never reaches the PC, not even for a moment. Copy a single snapshot and check it (`SHA256SUMS` without its `env.agent` line):

```bash
mkdir -p "$PCB/vps"
TS=2026-10-01_0826Z-verify
$VPS "tar -C /root/jarvis-backups --exclude=$TS/env.agent -cf - $TS" | tar -C "$PCB/vps" -xf -
(cd "$PCB/vps/$TS" && grep -v '  env\.agent$' SHA256SUMS | sha256sum -c --quiet - && echo MIRROR_OK)
```

Copy every snapshot that is not on the PC yet. This never overwrites or deletes anything:

```bash
for TS in $($VPS 'cd /root/jarvis-backups && ls -1d 2*/MANIFEST.txt | cut -d/ -f1'); do
  [ -e "$PCB/vps/$TS" ] || $VPS "tar -C /root/jarvis-backups --exclude=$TS/env.agent -cf - $TS" | tar -C "$PCB/vps" -xf -
done
```

The old `scp -r` loop copied every `2*` name, so an older mirror can also hold other tools' folders (old env copies, without `MANIFEST.txt`): delete those on the PC only after the user's OK.

The hand-made `2026-09-30_2340` snapshot has no `SHA256SUMS`. Check it by comparing `find . -type f -print0 | sort -z | xargs -0 sha256sum` on both sides.

### Restore drill (read-only; run after every backup)

```bash
$VPS 'D=$(ls -1d /root/jarvis-backups/2*/MANIFEST.txt | tail -1); D=${D%/MANIFEST.txt}; tar --compare -z -f $D/opt-jarvis-agent.tgz -C /opt 2>&1 | grep -v DEPLOYED_SHA | head; echo RESTORE_DRILL_DONE'
$VPS 'D=$(ls -1d /root/jarvis-backups/2*/MANIFEST.txt | tail -1); D=${D%/MANIFEST.txt}; cd $D && sha256sum -c --quiet SHA256SUMS && tar -tzf volumes.tgz >/dev/null && echo ARCHIVES_OK'
```

`D` is the newest `backup_vps.sh` folder: only those hold `MANIFEST.txt`, so another tool's `2*` folder is never taken. If nothing is printed before `RESTORE_DRILL_DONE`, the live `/opt/jarvis-agent` matches the backup. Right after a deploy, a list of differences is expected.

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

**4. Env files.** With `rollback.sh` (C-127), dry run first. It puts the env files back together with the image and the compose file they belong to, and only the env files that the services it recreates read. It checks the env layout and the shared values first, keeps the live `JARVIS_BRAIN`, `COMPOSE_PROFILES` and Desktop Link key lines, and moves a live `bridge.env` that the backup never had aside ([Rollback](#rollback)):

```bash
bash $T/rollback.sh ${B##*/} --services ui-bridge --with-env --dry-run   # each env file it replaces, keeps or moves aside
bash $T/rollback.sh ${B##*/} --services ui-bridge --with-env
```

- `.env.agent` (the backup's `env.agent`) goes back only with a rollback that recreates `agent` and `agent-svc` (`--services agent,agent-svc --with-env`, in vps mode only: in pc mode they stay stopped, K-72), never with the bridge alone: at their next restart they would meet it out of step with their image.
- Do not `install` the backup's env files by hand. A plain copy brings back the backup's `DESKTOP_AGENT_PUBLIC_KEY` and `DESKTOP_PC_PUBLIC_KEY` in `bridge.env` (and `DESKTOP_AGENT_SIGNING_KEY` with them in `.env.agent`), whose other halves are on the laptop, so the bridge's `/desktop` relay would switch off or refuse every PC-signed request; and it checks neither the env layout nor the shared values. If `rollback.sh` cannot run at all and you copy the env files of the services you recreate anyway (`.env.agent` only together with `agent` and `agent-svc`, as above), put the live lines back from the `pre-restore` backup above before any `up`: `JARVIS_BRAIN` and `COMPOSE_PROFILES` into `.env` (a `hostinger.env` from before C-126 has no `JARVIS_BRAIN`, and every compose command refuses without it; `grep -c '^JARVIS_BRAIN=..*' /opt/jarvis-agent/deploy/hostinger/.env` must say 1), the two public keys into `bridge.env`, and those two and `DESKTOP_AGENT_SIGNING_KEY` into `.env.agent`. Make `AGENT_TOKEN`, `BRIDGE_HMAC_SECRET` and the LiveKit pairs equal to the live values (the PC, the laptop and every service you do not recreate keep those), then recreate those services with `$C up -d --no-build --no-deps <services>` (the bridge: `ui-bridge`).
- Build args are baked into the frontend image, so for the frontend either roll the image back (step 1) or rebuild it.

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
