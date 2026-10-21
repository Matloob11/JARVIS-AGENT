# VPS tools readiness audit (REQ-FIX-2 item 13)

Cursor-lane read of `deploy/windows-local/TOOLS.md` (Claude owns that file; not edited).
Date: 2026-10-21. No secrets.

## Placement after VPS brain cutover (from TOOLS.md)

| # | Tool / area | Runs on | Notes |
|---|-------------|---------|--------|
| 1 | get_weather | VPS | OpenWeather; city from Desktop Node / USER_CITY (no ipinfo on VPS) |
| 2 | search_internet | VPS | DDG rate-limits on datacenter IPs |
| 3 | perform_web_research | VPS | |
| 4 | research + email | VPS | |
| 5 | get_formatted_datetime | VPS | |
| 6 | map / location tools | VPS | map_focus_update; device location separate |
| 7 | email | VPS | attachments from VPS media or encrypted laptop blob |
| 8 | download_images | VPS | |
| 9 | generate_image | VPS | |
| 10 | generate_qr_code | VPS | |
| 11 | download_youtube_media | VPS (+ laptop fallback on YT_BOT_CHECK) | K-71 / C-119 |
| 12 | SIM lookup | VPS | keys in .env.agent |
| 13 | reminders | VPS | C-125 live; reminder_due + ack |
| 14 | tasks / jobs | VPS | task_sync; Cursor fixed epoch due_at + thread-safe publish |
| 15 | plan progress | VPS | plan_progress flat; UI bar live |
| 16 | identity / voice enroll | VPS | voice_id_status progress |
| 17 | bug hunter / self-repair | VPS | apply refused on remote brain |
| 18 | analyze_camera | VPS (30 s) | browser frame else laptop webcam via node |

## Split / laptop-only (not counted in the 18, but related)

- analyze_screen: laptop capture, VPS analysis
- zip_files: routed per path (Windows path → laptop)
- ask_about_document, file access server: Desktop Node

## Gaps called out in TOOLS.md

- `/vortex/media` Archives/ not served → VPS zips not openable on laptop until K-73 change
- Media cards LAN notice vs `/vortex/media` (C-132)

## Cursor actions this pass

- UI: plan fixtures flat; tasks null due_at; reminder_due dedupe/ack; VoiceEnroll already reads voice_id_status
- Backend (Cursor lane): jarvis_task_manager epoch due_at + thread-safe publish; jarvis_reminders unique rem_* ids, no INFO text, mark_reminder_delivered(created_at=, time=)
