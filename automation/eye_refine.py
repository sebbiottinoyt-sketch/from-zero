import cv2, numpy as np, sys
def refine_eye(img, cx, cy, size):
    """img RGB; (cx,cy) stima approssimativa del centro occhio, size = larghezza stimata. Ritorna (cx,cy,rx,ry,conf)."""
    h, w = img.shape[:2]
    m = int(size * 1.3)
    x0, y0, x1, y1 = max(0, int(cx - m)), max(0, int(cy - m)), min(w, int(cx + m)), min(h, int(cy + m))
    crop = img[y0:y1, x0:x1]
    hsv = cv2.cvtColor(crop, cv2.COLOR_RGB2HSV).astype(np.float32)
    S, V = hsv[..., 1], hsv[..., 2]
    # bianco dell'occhio: chiaro e poco saturo rispetto alla pelle intorno
    skinV = np.median(V)
    white = ((V > max(150, skinV * 1.05)) & (S < 110)).astype(np.uint8)
    white = cv2.morphologyEx(white, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    n, lab, st, cen = cv2.connectedComponentsWithStats(white)
    best = []
    for i in range(1, n):
        x, y, ww, hh, area = st[i]
        if area < (size * 0.04) ** 2 * 3:
            continue
        ccx, ccy = cen[i]
        d = np.hypot(ccx + x0 - cx, ccy + y0 - cy)
        if d < size * 0.9 and ww < size * 1.6 and hh < size * 1.0:
            best.append((i, area, d))
    if not best:
        return None
    # componente principale + altre sullo stesso livello (sclera divisa dall'iride)
    best.sort(key=lambda b: -b[1] / (1 + b[2] / size))
    main = best[0][0]
    my = cen[main][1]
    keep = [b[0] for b in best if abs(cen[b[0]][1] - my) < size * 0.25]
    xs0 = min(st[i][0] for i in keep); ys0 = min(st[i][1] for i in keep)
    xs1 = max(st[i][0] + st[i][2] for i in keep); ys1 = max(st[i][1] + st[i][3] for i in keep)
    # l'iride e' scura: estendi verticalmente l'apertura cercando la zona non-pelle
    ex0, ex1 = xs0, xs1
    if len(keep) == 1:
        # una sola zona bianca: l'iride sta da un lato; allarga verso il lato piu' scuro
        bh0 = ys1 - ys0
        ext = int(bh0 * 1.0)
        band = V[ys0:ys1]
        left = band[:, max(0, xs0 - ext):xs0].mean() if xs0 > 0 else 999
        right = band[:, xs1:min(V.shape[1], xs1 + ext)].mean() if xs1 < V.shape[1] else 999
        if left < right:
            ex0 = max(0, xs0 - ext)
        else:
            ex1 = min(V.shape[1], xs1 + ext)
    bw, bh = ex1 - ex0, ys1 - ys0
    ecx = x0 + (ex0 + ex1) / 2
    # bordo inferiore = fine del bianco; bordo superiore = linea scura delle ciglia sopra il bianco
    bottom = ys1 + bw * 0.05
    xa, xb = int(ex0 + bw * 0.2), int(ex1 - bw * 0.2)
    top = ys0
    y = ys0 - 1
    limit = max(0, int(ys0 - bw * 0.45))
    while y >= limit:
        row = V[y, xa:max(xa + 1, xb)]
        if row.size and (row < skinV * 0.55).mean() > 0.35:
            top = y
        elif top < ys0 and y < top - 2:
            break
        y -= 1
    top = min(top, ys0 - bw * 0.04)
    ecy = y0 + (top + bottom) / 2
    rx = bw / 2 * 1.1
    ry = max((bottom - top) / 2 * 0.97, bw * 0.24)
    conf = min(1.0, sum(st[i][4] for i in keep) / (bw * bh + 1) * 2)
    # poco credibile se lontano dalla stima o troppo piccolo/grande
    if np.hypot(ecx - cx, ecy - cy) > size * 0.6 or bw < size * 0.35 or bw > size * 1.6:
        conf *= 0.3
    return round(ecx), round(ecy), round(rx), round(ry), round(conf, 2)

