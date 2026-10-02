"""Visual diff loop for overview_replica.html vs the reference PNG.

usage: python diff.py <iter_label> [--noshot] [--query ?still=1]
Writes: shots/<label>.png, heat/<label>_heat.png, metrics/<label>.json
"""
import sys, os, json, math, time
import numpy as np, cv2
from PIL import Image
from scipy.ndimage import gaussian_filter

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
REF = os.path.join(HERE, 'ref.png')
HTML = os.path.join(BASE, 'overview_replica.html')
FOR = json.load(open(os.path.join(BASE, 'forensics.json'), encoding='utf-8'))
W, H = 1672, 941

REGIONS = {
    # name: (x, y, w, h)
    'sidebar':      (0, 0, 272, 941),
    'header':       (272, 0, 1400, 114),
    'hero':         (295, 116, 830, 474),
    'orb':          (304, 116, 438, 352),   # orb square inside hero (r~230 clipped)
    'hero_text':    (735, 180, 280, 250),
    'hero_buttons': (395, 470, 636, 70),
    'quickstats':   (295, 606, 411, 306),
    'activity':     (721, 607, 404, 305),
    'panel':        (1143, 114, 512, 800),
    'panel_tabs':   (1144, 115, 510, 66),
    'confirm':      (1162, 202, 476, 268),
    'chat':         (1160, 495, 482, 240),
    'composer':     (1158, 770, 481, 129),
    'page_bg_gaps': None,  # computed mask
}


def load(p):
    return np.asarray(Image.open(p).convert('RGB')).astype(np.float32)


def shoot(out_png, query='?still=1', wait_ms=250, evaluate=None):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(args=['--force-color-profile=srgb'])
        ctx = b.new_context(viewport={'width': W, 'height': H}, device_scale_factor=1)
        pg = ctx.new_page()
        msgs = []
        pg.on('console', lambda m: msgs.append(m.type + ': ' + m.text))
        pg.on('pageerror', lambda e: msgs.append('PAGEERROR: ' + str(e)))
        url = 'file:///' + HTML.replace('\\', '/') + query
        pg.goto(url)
        try:
            pg.wait_for_function('window.__replicaReady === true', timeout=20000)
        except Exception as e:
            msgs.append('WAIT-TIMEOUT ' + str(e))
        if evaluate:
            pg.evaluate(evaluate)
        pg.wait_for_timeout(wait_ms)
        pg.screenshot(path=out_png)
        b.close()
    return msgs


def lum(a):
    return 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]


def ssim_map(x, y):
    x = x.astype(np.float64); y = y.astype(np.float64)
    C1 = (0.01 * 255) ** 2; C2 = (0.03 * 255) ** 2
    s = 1.5
    mx = gaussian_filter(x, s, truncate=3.5); my = gaussian_filter(y, s, truncate=3.5)
    sxx = gaussian_filter(x * x, s, truncate=3.5) - mx * mx
    syy = gaussian_filter(y * y, s, truncate=3.5) - my * my
    sxy = gaussian_filter(x * y, s, truncate=3.5) - mx * my
    return ((2 * mx * my + C1) * (2 * sxy + C2)) / ((mx * mx + my * my + C1) * (sxx + syy + C2))


def to_lab(a):
    u8 = np.clip(a, 0, 255).astype(np.uint8)
    lab = cv2.cvtColor(u8, cv2.COLOR_RGB2LAB).astype(np.float32)
    lab[..., 0] *= 100.0 / 255.0
    lab[..., 1] -= 128; lab[..., 2] -= 128
    return lab


def de2000(l1, l2):
    L1, a1, b1 = l1; L2, a2, b2 = l2
    C1 = math.hypot(a1, b1); C2 = math.hypot(a2, b2); Cb = (C1 + C2) / 2
    G = 0.5 * (1 - math.sqrt(Cb ** 7 / (Cb ** 7 + 25 ** 7)))
    a1p = (1 + G) * a1; a2p = (1 + G) * a2
    C1p = math.hypot(a1p, b1); C2p = math.hypot(a2p, b2)
    h1p = math.degrees(math.atan2(b1, a1p)) % 360; h2p = math.degrees(math.atan2(b2, a2p)) % 360
    dLp = L2 - L1; dCp = C2p - C1p
    dhp = h2p - h1p
    if C1p * C2p == 0: dhp = 0
    elif dhp > 180: dhp -= 360
    elif dhp < -180: dhp += 360
    dHp = 2 * math.sqrt(C1p * C2p) * math.sin(math.radians(dhp / 2))
    Lbp = (L1 + L2) / 2; Cbp = (C1p + C2p) / 2
    if C1p * C2p == 0: hbp = h1p + h2p
    elif abs(h1p - h2p) <= 180: hbp = (h1p + h2p) / 2
    elif h1p + h2p < 360: hbp = (h1p + h2p + 360) / 2
    else: hbp = (h1p + h2p - 360) / 2
    T = 1 - 0.17 * math.cos(math.radians(hbp - 30)) + 0.24 * math.cos(math.radians(2 * hbp)) + 0.32 * math.cos(math.radians(3 * hbp + 6)) - 0.20 * math.cos(math.radians(4 * hbp - 63))
    dth = 30 * math.exp(-((hbp - 275) / 25) ** 2)
    Rc = 2 * math.sqrt(Cbp ** 7 / (Cbp ** 7 + 25 ** 7))
    Sl = 1 + 0.015 * (Lbp - 50) ** 2 / math.sqrt(20 + (Lbp - 50) ** 2)
    Sc = 1 + 0.045 * Cbp; Sh = 1 + 0.015 * Cbp * T
    Rt = -math.sin(math.radians(2 * dth)) * Rc
    return math.sqrt((dLp / Sl) ** 2 + (dCp / Sc) ** 2 + (dHp / Sh) ** 2 + Rt * (dCp / Sc) * (dHp / Sh))


# ---------------------------------------------------------------- ink boxes
def targets():
    T = []
    def add(tid, box, mode='full', pad=(6, 5), thr=0.45):
        T.append(dict(id=tid, box=box, mode=mode, pad=pad, thr=thr))
    for e in FOR['elements']:
        eid = e['id']
        t = e.get('text')
        if t and t.get('ink_bbox'):
            b = t['ink_bbox']; box = (b['x'], b['y'], b['w'], b['h'])
            mode = 'full'
            if eid == 'header.avatar': mode = 'center'
            if eid == 'header.greeting.name': mode = 'left'
            if eid == 'panel.chat.user.avatar': mode = 'center'
            add(eid + ':text', box, mode)
        ic = e.get('icon')
        if ic and ic.get('bbox'):
            b = ic['bbox']; add(eid + ':icon', (b['x'], b['y'], b['w'], b['h']), 'full', pad=(4, 4), thr=0.4)
        elif e.get('type') == 'icon' and e.get('bbox'):
            b = e['bbox']; add(eid + ':icon', (b['x'], b['y'], b['w'], b['h']), 'full', pad=(4, 4), thr=0.4)
        for k in ('value', 'label', 'title', 'subtitle', 'time'):
            v = e.get(k)
            if isinstance(v, dict) and v.get('ink_bbox'):
                b = v['ink_bbox']; add(eid + ':' + k, (b['x'], b['y'], b['w'], b['h']))
        for i, ln in enumerate(e.get('lines', []) or []):
            b = ln['ink_bbox']; add('%s:line%d' % (eid, i + 1), (b['x'], b['y'], b['w'], b['h']))
    return T


def ink_box(L, box, pad, thr):
    x, y, w, h = box
    X0, Y0 = max(0, x - pad[0]), max(0, y - pad[1])
    X1, Y1 = min(W, x + w + pad[0]), min(H, y + h + pad[1])
    p = L[Y0:Y1, X0:X1]
    if p.size == 0: return None
    # background = smooth low percentile of the border ring of the window
    ring = np.concatenate([p[0, :], p[-1, :], p[:, 0], p[:, -1]])
    bg = float(np.median(ring))
    hi = float(np.percentile(p, 99.0))
    if hi - bg < 12: return None
    m = p > bg + thr * (hi - bg)
    n, lab, st, _ = cv2.connectedComponentsWithStats(m.astype(np.uint8), connectivity=8)
    keep = np.zeros_like(m)
    for i in range(1, n):
        if st[i, cv2.CC_STAT_AREA] >= 2:
            keep |= lab == i
    ys, xs = np.nonzero(keep)
    if len(xs) == 0: return None
    return (X0 + int(xs.min()), Y0 + int(ys.min()), int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1))


def compare_boxes(A, B):
    LA, LB = lum(A), lum(B)
    out = []
    for t in targets():
        ra = ink_box(LA, t['box'], t['pad'], t['thr'])
        rb = ink_box(LB, t['box'], t['pad'], t['thr'])
        if ra is None or rb is None:
            out.append(dict(id=t['id'], ref=ra, rep=rb, err=None)); continue
        if t['mode'] == 'center':
            dx = (rb[0] + rb[2] / 2) - (ra[0] + ra[2] / 2); dy = (rb[1] + rb[3] / 2) - (ra[1] + ra[3] / 2)
            out.append(dict(id=t['id'], ref=ra, rep=rb, dx=dx, dy=dy, dw=0, dh=0, err=max(abs(dx), abs(dy))))
        elif t['mode'] == 'left':
            dx = rb[0] - ra[0]; dy = rb[1] - ra[1]; dh = rb[3] - ra[3]
            out.append(dict(id=t['id'], ref=ra, rep=rb, dx=dx, dy=dy, dw=0, dh=dh, err=max(abs(dx), abs(dy), abs(dh))))
        else:
            dx = rb[0] - ra[0]; dy = rb[1] - ra[1]; dw = rb[2] - ra[2]; dh = rb[3] - ra[3]
            # error = worst edge displacement
            e = max(abs(dx), abs(dy), abs(dx + dw), abs(dy + dh))
            out.append(dict(id=t['id'], ref=ra, rep=rb, dx=dx, dy=dy, dw=dw, dh=dh, err=e))
    return out


# ---------------------------------------------------------------- containers
CONTAINERS = {
    'hero': (295, 116, 830, 474), 'quickstats': (295, 606, 411, 306), 'activity': (721, 607, 404, 305),
    'panel': (1143, 114, 512, 800), 'nav_active': (16, 185, 241, 61),
    'pill_system': (764, 29, 163, 54), 'pill_devices': (943, 29, 205, 54), 'pill_voice': (1163, 29, 153, 54),
    'btn_start': (403, 476, 217, 56), 'btn_run': (635, 476, 193, 56), 'btn_micoff': (843, 476, 180, 56),
    'tile_cpu': (315, 682, 180, 91), 'tile_mem': (510, 682, 180, 91), 'tile_disk': (315, 787, 180, 95), 'tile_dev': (510, 787, 180, 95),
    'confirm': (1162, 202, 476, 268), 'req_pill': (1471, 223, 146, 30), 'code': (1184, 308, 433, 80),
    'deny': (1184, 400, 209, 49), 'approve': (1408, 400, 209, 49),
    'bubble_user': (1229, 499, 409, 79), 'bubble_ai': (1229, 601, 409, 130),
    'composer': (1158, 770, 481, 129), 'input': (1173, 785, 450, 58), 'send': (1570, 787, 54, 54),
    'avatar': (1417, 27, 59, 59),
}


def edge_pos(L, box, side, search=7):
    x, y, w, h = box
    if side in ('l', 'r'):
        ys = slice(y + int(h * 0.3), y + int(h * 0.7))
        xc = x if side == 'l' else x + w - 1
        xs = np.arange(xc - search, xc + search + 1)
        xs = xs[(xs > 0) & (xs < W - 1)]
        prof = L[ys][:, xs].mean(0)
        g = np.abs(np.gradient(prof))
        return float(xs[int(np.argmax(g))])
    else:
        xs = slice(x + int(w * 0.3), x + int(w * 0.7))
        yc = y if side == 't' else y + h - 1
        ys = np.arange(yc - search, yc + search + 1)
        ys = ys[(ys > 0) & (ys < H - 1)]
        prof = L[ys][:, xs].mean(1)
        g = np.abs(np.gradient(prof))
        return float(ys[int(np.argmax(g))])


def compare_containers(A, B):
    LA, LB = lum(A), lum(B)
    out = {}
    for k, box in CONTAINERS.items():
        d = {}
        for s in 'lrtb':
            d[s] = edge_pos(LB, box, s) - edge_pos(LA, box, s)
        out[k] = d
    return out


# ---------------------------------------------------------------- main
def region_stats(A, B, D, S, name, box):
    x, y, w, h = box
    a = A[y:y + h, x:x + w]; b = B[y:y + h, x:x + w]
    la = to_lab(a.reshape(-1, 1, 3)).reshape(-1, 3).mean(0)
    lb = to_lab(b.reshape(-1, 1, 3)).reshape(-1, 3).mean(0)
    return dict(mae=float(D[y:y + h, x:x + w].mean()), ssim=float(S[y:y + h, x:x + w].mean()),
                mean_ref='#%02x%02x%02x' % tuple(int(round(v)) for v in a.reshape(-1, 3).mean(0)),
                mean_rep='#%02x%02x%02x' % tuple(int(round(v)) for v in b.reshape(-1, 3).mean(0)),
                dE_mean=round(de2000(tuple(la), tuple(lb)), 2))


def run(label, noshot=False, query='?still=1'):
    os.makedirs(os.path.join(HERE, 'shots'), exist_ok=True)
    os.makedirs(os.path.join(HERE, 'heat'), exist_ok=True)
    os.makedirs(os.path.join(HERE, 'metrics'), exist_ok=True)
    shot = os.path.join(HERE, 'shots', label + '.png')
    msgs = []
    if not noshot:
        msgs = shoot(shot, query)
    A = load(REF); B = load(shot)
    D = np.abs(A - B).mean(2)
    S = ssim_map(lum(A), lum(B))
    # per-pixel perceptual error (CIE76 in Lab)
    dE = np.sqrt(((to_lab(A) - to_lab(B)) ** 2).sum(2))
    res = dict(label=label, time=time.strftime('%Y-%m-%d %H:%M:%S'), console=msgs,
               mae=float(D.mean()), rmse=float(np.sqrt(((A - B) ** 2).mean())),
               psnr=float(20 * math.log10(255 / max(1e-9, np.sqrt(((A - B) ** 2).mean())))),
               ssim=float(S.mean()), dE76_mean=float(dE.mean()),
               pct_px_dE_lt_5=float((dE < 5).mean() * 100), pct_px_dE_lt_10=float((dE < 10).mean() * 100))
    regs = {}
    for k, box in REGIONS.items():
        if box is None: continue
        regs[k] = region_stats(A, B, D, S, k, box)
    # page background (gaps outside all cards)
    m = np.zeros((H, W), bool)
    m[:, 272:] = True
    for k in ('hero', 'quickstats', 'activity', 'panel'):
        x, y, w, h = REGIONS[k]; m[y - 2:y + h + 2, x - 2:x + w + 2] = False
    m[25:90, 300:1660] = False  # header content band
    regs['page_bg_gaps'] = dict(mae=float(D[m].mean()), ssim=float(S[m].mean()),
                                mean_ref='#%02x%02x%02x' % tuple(int(round(v)) for v in A[m].mean(0)),
                                mean_rep='#%02x%02x%02x' % tuple(int(round(v)) for v in B[m].mean(0)))
    res['regions'] = regs
    boxes = compare_boxes(A, B)
    errs = [b['err'] for b in boxes if b.get('err') is not None]
    res['ink'] = dict(n=len(boxes), measured=len(errs),
                      within1=sum(e <= 1 for e in errs), within2=sum(e <= 2 for e in errs), within3=sum(e <= 3 for e in errs),
                      max=max(errs) if errs else None, mean=float(np.mean(errs)) if errs else None)
    res['ink_detail'] = boxes
    cont = compare_containers(A, B)
    cerr = [abs(v) for d in cont.values() for v in d.values()]
    res['containers'] = dict(max=max(cerr), mean=float(np.mean(cerr)), within1=sum(e <= 1 for e in cerr), n=len(cerr))
    res['container_detail'] = cont
    json.dump(res, open(os.path.join(HERE, 'metrics', label + '.json'), 'w'), indent=1)
    # heatmap: mean abs diff x4, inferno
    hm = np.clip(D * 4, 0, 255).astype(np.uint8)
    hm = cv2.applyColorMap(hm, cv2.COLORMAP_INFERNO)[..., ::-1]
    Image.fromarray(hm).save(os.path.join(HERE, 'heat', label + '_heat.png'))
    return res


def summarize(res):
    print('== %s  MAE %.2f  RMSE %.2f  PSNR %.2f  SSIM %.4f  dE76 %.2f  px dE<5 %.1f%%  dE<10 %.1f%%' % (
        res['label'], res['mae'], res['rmse'], res['psnr'], res['ssim'], res['dE76_mean'], res['pct_px_dE_lt_5'], res['pct_px_dE_lt_10']))
    for c in res['console']:
        print('   console:', c[:200])
    for k, r in res['regions'].items():
        print('  %-13s MAE %6.2f  SSIM %.3f  mean ref %s rep %s  dE %s' % (k, r['mae'], r['ssim'], r['mean_ref'], r['mean_rep'], r.get('dE_mean', '')))
    i = res['ink']
    print('  ink boxes: %d measured / %d; <=1px %d, <=2px %d, <=3px %d, max %s, mean %.2f' % (i['measured'], i['n'], i['within1'], i['within2'], i['within3'], i['max'], i['mean'] or 0))
    c = res['containers']
    print('  container edges: n %d, <=1px %d, max %.1f, mean %.2f' % (c['n'], c['within1'], c['max'], c['mean']))


if __name__ == '__main__':
    label = sys.argv[1] if len(sys.argv) > 1 else 'iter'
    noshot = '--noshot' in sys.argv
    res = run(label, noshot)
    summarize(res)
    if '--detail' in sys.argv:
        for b in sorted(res['ink_detail'], key=lambda b: -(b['err'] or 99)):
            print('   %-42s err %-5s ref %-22s rep %-22s' % (b['id'], b.get('err'), b['ref'], b['rep']))
        for k, d in res['container_detail'].items():
            if max(abs(v) for v in d.values()) > 1:
                print('   box %-14s %s' % (k, d))
