"""Prova il collegamento a Instagram e trova l'ID giusto dell'account.

Si lancia da GitHub: Actions -> "Prova Instagram" -> Run workflow.
Non stampa mai il token.
"""
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import config  # noqa: E402

import re

TOKEN = re.sub(r"\s+", "", os.environ.get("IG_TOKEN", ""))
TOKEN_RE = re.compile(r"(EAA|IG)[A-Za-z0-9_\-]{10,}")


def hide(text):
    """Non mostrare mai il token nei log (il repository e' pubblico)."""
    return TOKEN_RE.sub("[TOKEN NASCOSTO]", str(text))
cfg = config()["instagram"]
VER = cfg.get("api_version", "v26.0")
USER_ID = str(cfg.get("user_id") or "").strip()


def get(host, path, **params):
    params["access_token"] = TOKEN
    try:
        r = requests.get(f"{host}/{VER}/{path}", params=params, timeout=40)
        return r.status_code, r.json()
    except Exception as e:  # noqa: BLE001
        return 0, {"error": str(e)}


def short(d):
    if isinstance(d, dict) and "error" in d:
        e = d["error"]
        return hide(e.get("message", str(e))[:160] if isinstance(e, dict) else str(e)[:160])
    return hide(str(d)[:300])


print("=== PROVA COLLEGAMENTO INSTAGRAM ===")
if not TOKEN:
    sys.exit("IG_TOKEN mancante: aggiungilo in Settings -> Secrets and variables -> Actions")
print(f"Token presente ({len(TOKEN)} caratteri). user_id in config.json: {USER_ID or '(vuoto)'}")
if not re.fullmatch(r"[A-Za-z0-9]+", TOKEN):
    bad = sorted({c for c in TOKEN if not c.isalnum()})
    print(f"ATTENZIONE: il token contiene caratteri non validi {bad}: e' stato copiato male "
          "(usa il pulsante Copia accanto al token, non la selezione del testo)")
print()

found = []
FB, IG = "https://graph.facebook.com", "https://graph.instagram.com"

# 1) ID scritto in config.json, con il login unificato
if USER_ID:
    s, d = get(FB, USER_ID, fields="id,username,followers_count,media_count")
    print(f"[1] graph.facebook.com /{USER_ID}: {'OK ' + str(d) if s == 200 and d.get('username') else 'NO - ' + short(d)}")
    if s == 200 and d.get("username"):
        found.append(("https://graph.facebook.com", d["id"], d["username"], d.get("followers_count")))

# 2) Login di Instagram (token che funziona su graph.instagram.com)
s, d = get(IG, "me", fields="user_id,username,followers_count,media_count")
print(f"[2] graph.instagram.com /me: {'OK ' + str(d) if s == 200 and d.get('username') else 'NO - ' + short(d)}")
if s == 200 and d.get("username"):
    found.append(("https://graph.instagram.com", d.get("user_id") or d.get("id"), d["username"],
                  d.get("followers_count")))

# 3) Login unificato: chi è il token e quali account Instagram vede
s, d = get(FB, "me", fields="id,name")
print(f"[3] graph.facebook.com /me: {'OK ' + str(d) if s == 200 else 'NO - ' + short(d)}")
s, d = get(FB, "me/accounts", fields="name,instagram_business_account{id,username,followers_count}")
if s == 200:
    for p in d.get("data", []):
        iba = p.get("instagram_business_account")
        if iba:
            print(f"    pagina {p.get('name')}: Instagram {iba}")
            found.append(("https://graph.facebook.com", iba["id"], iba.get("username"), iba.get("followers_count")))
for edge in ("instagram_accounts", "assigned_instagram_accounts"):
    s, d = get(FB, f"me/{edge}", fields="id,username,followers_count")
    if s == 200 and d.get("data"):
        for a in d["data"]:
            print(f"    {edge}: {a}")
            if a.get("username"):
                found.append(("https://graph.facebook.com", a["id"], a.get("username"), a.get("followers_count")))

print("\n=== RISULTATO ===")
if not found:
    print("Nessun account Instagram raggiungibile con questo token.")
    print("Controlla: tester accettato su instagram.com, token generato dopo, permessi instagram_basic.")
    sys.exit(1)
host, uid, uname, fol = found[0]
print(f"FUNZIONA: @{uname} (follower: {fol})")
print("In config.json -> instagram metti:")
print(f'  "username": "{uname}",')
print(f'  "user_id": "{uid if "facebook" in host else ""}",')
print(f'  "api_host": "{host}",')

# prova lettura media e commenti
path = f"{uid}/media" if "facebook" in host else "me/media"
s, d = get(host, path, fields="id,media_product_type,like_count,comments_count,permalink", limit=3)
if s == 200:
    items = d.get("data", [])
    print(f"\nUltimi post letti: {len(items)}")
    for m in items:
        print(f"  {m.get('media_product_type')} like={m.get('like_count')} commenti={m.get('comments_count')} "
              f"{m.get('permalink')}")
    if items:
        s2, d2 = get(host, f"{items[0]['id']}/comments", fields="username,text", limit=3)
        print("Lettura commenti:", "OK" if s2 == 200 else "NO - " + short(d2))
else:
    print("Lettura post: NO - " + short(d))
