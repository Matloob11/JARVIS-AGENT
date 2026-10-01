# JARVIS-AGENT: rules for Claude Code

This repo is worked on by two agents at the same time: Claude Code (terminal, SSH, deploys) and Cursor (IDE). The user wants every existing feature working; nothing gets removed.

- The shared plan is `plan.md`. Claude takes only `C-` tasks. Cursor takes `X-` tasks.
- Lane ownership is in `plan.md` section 2.1. Claude owns `src/core/**`, `services/ai_core/**`, `services/utils/**`, `deploy/**`, `scripts/**`, root config (`requirements*.txt`, `pyproject.toml`, `.env*`, `Dockerfile*`, `docker-compose.yml`, `.github/**`) and everything on the VPS. Don't edit Cursor-lane files; write a request in `plan.md` section 11 instead.
- Claim a task with `DOING (claude, <date time PKT>)` on its status line. Append each finished task at the end of `done_highlight.md` using that file's template.
- Git: commit with explicit paths only, message `[claude] C-NN: ...`. Only Claude pushes to `origin` (github.com/Matloob11/JARVIS-AGENT). Never `git add -A`, stash, hard reset, clean, checkout another branch, or force push, because Cursor works in the same working tree.
- VPS 187.127.123.235: only Claude changes it. Back up first. Touch only Docker project `jarvis` and `/etc/nginx/conf.d/jarvis.conf`. Other apps on that server must keep working.
- Only one Windows agent worker may run. Respect the `RUNTIME LOCK` line in `plan.md` section 2.6.
- Never print or commit secrets from `.env` files.
