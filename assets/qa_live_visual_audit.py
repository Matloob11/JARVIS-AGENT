"""After owner login: multi-viewport visual audit of the live neon UI."""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = os.environ.get("JARVIS_LIVE_URL", "https://jarvis.srv1802669.hstgr.cloud/#/overview")
OUT = Path("assets/qa-visual-shots")
REPORT = Path("assets/qa-visual-report.json")
STATE = Path("assets/qa-live-storage.json")
LOGIN_WAIT = int(os.environ.get("JARVIS_QA_LOGIN_WAIT", "600"))

VIEWPORTS = [
    ("phone-390", 390, 844),
    ("phone-430", 430, 932),
    ("tablet-768", 768, 1024),
    ("laptop-1280", 1280, 800),
    ("desktop-1536", 1536, 900),
    ("wide-1672", 1672, 941),
]

PAGES = [
    ("overview", "#/overview"),
    ("agents", "#/agents"),
    ("activity", "#/activity"),
    ("settings", "#/settings"),
]


def _ts() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def _base(url: str) -> str:
    return url.split("#")[0].rstrip("/")


def wait_ready(page) -> bool:
    deadline = time.time() + LOGIN_WAIT
    while time.time() < deadline:
        try:
            if page.locator('[data-testid="app-ready"]').count() and page.locator(
                '[data-testid="app-ready"]'
            ).first.is_visible():
                return True
            if page.get_by_text("System Online", exact=False).count():
                return True
        except Exception:
            pass
        time.sleep(1.2)
    return False


def metrics(page) -> dict:
    return page.evaluate(
        """() => {
      const doc = document.documentElement;
      const body = document.body;
      const overflowX = Math.max(doc.scrollWidth, body.scrollWidth) > window.innerWidth + 2;
      const overflowY = Math.max(doc.scrollHeight, body.scrollHeight) > window.innerHeight + 2;
      const texts = Array.from(document.querySelectorAll('h1,h2,h3,p,span,button,a,label'))
        .slice(0, 80)
        .map(el => {
          const r = el.getBoundingClientRect();
          const cs = getComputedStyle(el);
          const fs = parseFloat(cs.fontSize || '0');
          const clipped = el.scrollWidth > el.clientWidth + 1;
          return {
            t: (el.innerText || '').trim().slice(0, 60),
            fs,
            clipped,
            w: Math.round(r.width),
            h: Math.round(r.height),
            opacity: cs.opacity,
          };
        })
        .filter(x => x.t && x.w > 0 && x.h > 0);
      const tiny = texts.filter(x => x.fs > 0 && x.fs < 11).slice(0, 12);
      const clipped = texts.filter(x => x.clipped).slice(0, 12);
      const pills = Array.from(document.querySelectorAll('[data-testid^="chip-"], .status-pill, .page-header-pill'));
      const overlapping = [];
      // naive overlap among header pills
      for (let i = 0; i < pills.length; i++) {
        for (let j = i + 1; j < pills.length; j++) {
          const a = pills[i].getBoundingClientRect();
          const b = pills[j].getBoundingClientRect();
          const hit = !(a.right < b.left || a.left > b.right || a.bottom < b.top || a.top > b.bottom);
          if (hit && a.width && b.width) overlapping.push([pills[i].innerText?.slice(0,40), pills[j].innerText?.slice(0,40)]);
        }
      }
      return {
        inner: { w: window.innerWidth, h: window.innerHeight },
        overflowX,
        overflowY,
        scrollW: Math.max(doc.scrollWidth, body.scrollWidth),
        scrollH: Math.max(doc.scrollHeight, body.scrollHeight),
        tinyText: tiny,
        clippedText: clipped,
        pillOverlap: overlapping.slice(0, 6),
        title: document.title,
      };
    }"""
    )


def shot(page, name: str) -> str:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}.png"
    page.screenshot(path=str(path), full_page=False)
    return str(path)


def main() -> int:
    user = os.environ.get("JARVIS_BASIC_USER", "").strip()
    password = os.environ.get("JARVIS_BASIC_PASS", "").strip()
    http_creds = {"username": user, "password": password} if user and password else None

    findings: list[dict] = []
    print(f"[{_ts()}] visual audit starting", flush=True)
    print("Login in the Chromium window if needed…", flush=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=30)
        ctx_kwargs = {
            "viewport": {"width": 1536, "height": 900},
            "ignore_https_errors": True,
        }
        if http_creds:
            ctx_kwargs["http_credentials"] = http_creds
        if STATE.exists():
            ctx_kwargs["storage_state"] = str(STATE)
        context = browser.new_context(**ctx_kwargs)
        page = context.new_page()

        try:
            page.goto(URL, wait_until="domcontentloaded", timeout=120_000)
        except Exception as exc:
            print(f"NAV_ERR {str(exc)[:160]}", flush=True)
            page.set_content(
                f"<html><body style='font-family:Segoe UI;padding:32px;background:#0b1220;color:#e8f1ff'>"
                f"<h1>Visual QA</h1><p><a style='color:#4cc9f0' href='{URL}'>{URL}</a></p>"
                f"<p>Login here, then wait — audit auto-starts.</p></body></html>"
            )

        if not wait_ready(page):
            shot(page, "00-timeout")
            REPORT.write_text(
                json.dumps({"ts": _ts(), "ok": False, "error": "login_timeout"}, indent=2),
                encoding="utf-8",
            )
            print("LOGIN_TIMEOUT", flush=True)
            time.sleep(30)
            browser.close()
            return 2

        print("DASHBOARD_READY", flush=True)
        try:
            context.storage_state(path=str(STATE))
        except Exception:
            pass

        base = _base(page.url)
        for vp_name, w, h in VIEWPORTS:
            page.set_viewport_size({"width": w, "height": h})
            time.sleep(0.4)
            for page_name, hash_path in PAGES:
                page.goto(f"{base}/{hash_path}".replace("//#", "/#"), wait_until="domcontentloaded", timeout=60_000)
                # hash routes: base#/x
                page.goto(f"{base}{hash_path}", wait_until="domcontentloaded", timeout=60_000)
                time.sleep(0.7)
                name = f"{vp_name}__{page_name}"
                path = shot(page, name)
                m = metrics(page)
                status = "PASS"
                notes = []
                if m.get("overflowX"):
                    status = "FAIL"
                    notes.append("horizontal overflow")
                if m.get("pillOverlap"):
                    status = "FAIL"
                    notes.append(f"pill overlap {m['pillOverlap'][:2]}")
                if m.get("tinyText"):
                    status = "WARN" if status == "PASS" else status
                    notes.append(f"tiny text <11px x{len(m['tinyText'])}")
                if m.get("clippedText"):
                    status = "WARN" if status == "PASS" else status
                    notes.append(f"clipped text x{len(m['clippedText'])}")
                # header truncation heuristic
                if page_name == "overview" and page.get_by_text("JARVIS & ANN", exact=False).count():
                    # subtitle often truncates — note as WARN when viewport narrow
                    if w < 1400:
                        notes.append("header subtitle likely truncated")
                        if status == "PASS":
                            status = "WARN"
                finding = {
                    "viewport": vp_name,
                    "page": page_name,
                    "status": status,
                    "notes": notes,
                    "shot": path,
                    "metrics": {
                        "overflowX": m.get("overflowX"),
                        "overflowY": m.get("overflowY"),
                        "scrollW": m.get("scrollW"),
                        "inner": m.get("inner"),
                        "tiny_n": len(m.get("tinyText") or []),
                        "clipped_n": len(m.get("clippedText") or []),
                        "pillOverlap": m.get("pillOverlap"),
                    },
                }
                findings.append(finding)
                print(f"[{status}] {vp_name}/{page_name}: {', '.join(notes) or 'ok'}", flush=True)

            # right-rail tabs — on phone the panel is the Chat sheet
            page.goto(f"{base}#/overview", wait_until="domcontentloaded")
            time.sleep(0.4)
            if w < 1180:
                try:
                    page.locator('[data-testid="mobile-tab-chat"]').click(timeout=2500)
                    time.sleep(0.4)
                except Exception:
                    pass
            for tab, tid in (
                ("Approvals", "panel-tab-approvals"),
                ("Tools", "panel-tab-tools"),
                ("Chat", "panel-tab-chat"),
            ):
                try:
                    loc = page.locator(f'[data-testid="{tid}"]')
                    if loc.count():
                        loc.first.click(timeout=2500)
                    else:
                        page.locator('[data-testid="chat-composer-input"]').wait_for(timeout=1500)
                        page.get_by_role("button", name=tab, exact=True).first.click(timeout=2500)
                    time.sleep(0.45)
                    path = shot(page, f"{vp_name}__tab-{tab.lower()}")
                    findings.append(
                        {
                            "viewport": vp_name,
                            "page": f"tab-{tab.lower()}",
                            "status": "PASS",
                            "notes": [],
                            "shot": path,
                            "metrics": {},
                        }
                    )
                    print(f"[PASS] {vp_name}/tab-{tab}", flush=True)
                except Exception as exc:
                    findings.append(
                        {
                            "viewport": vp_name,
                            "page": f"tab-{tab.lower()}",
                            "status": "FAIL",
                            "notes": [str(exc)[:120]],
                            "shot": shot(page, f"{vp_name}__tab-{tab.lower()}-fail"),
                            "metrics": {},
                        }
                    )
                    print(f"[FAIL] {vp_name}/tab-{tab}", flush=True)

        fails = sum(1 for f in findings if f["status"] == "FAIL")
        warns = sum(1 for f in findings if f["status"] == "WARN")
        report = {
            "ts": _ts(),
            "ok": fails == 0,
            "fails": fails,
            "warns": warns,
            "findings": findings,
        }
        REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"REPORT {REPORT} FAIL={fails} WARN={warns}", flush=True)
        time.sleep(45)
        browser.close()
        return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
