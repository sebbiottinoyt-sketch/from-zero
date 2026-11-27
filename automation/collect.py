"""Legge Instagram dopo l'ultimo episodio: follower, like, voti e classifica dei fan.

Scrive data/collect_epNN.json (NN = episodio successivo) con tutto quello che serve al copione.
"""
import os
import re
import sys

import ig
from common import DATA, MOCK, config, log, save_json, state, save_state

MENTION = re.compile(r"@([A-Za-z0-9._]{1,30})")


def score(comments, options, cfg, own):
    """Voti (un voto per persona, vale l'ultimo) e punti dei fan."""
    rw = cfg["rewards"]
    own = own.lower()
    blocked = {u.lower().lstrip("@") for u in rw.get("blocked_users", [])}
    bad_words = [w.lower() for w in rw.get("blocked_words", [])]
    votes_by_user = {}
    users = {}
    for c in sorted(comments, key=lambda c: c.get("timestamp", "")):
        u = (c.get("username") or "").lower()
        if not u or u == own or u in blocked:
            continue
        text = c.get("text") or ""
        rec = users.setdefault(u, {"username": c.get("username"), "tags": set(), "likes": 0, "voted": False,
                                   "comments": 0})
        rec["comments"] += 1
        rec["likes"] += int(c.get("like_count") or 0)
        for m in MENTION.findall(text):
            m = m.lower().rstrip(".")
            if m not in (own, u):
                rec["tags"].add(m)
        # voto: la parola dell'opzione da sola (FIX, wash, Fix!...)
        hits = [o for o in options if re.search(rf"(?<![A-Za-z0-9]){re.escape(o)}(?![A-Za-z0-9])", text, re.I)]
        if len(hits) == 1:
            votes_by_user[u] = hits[0]
            rec["voted"] = True
            c["_option"] = hits[0]
    votes = {o: 0 for o in options}
    for o in votes_by_user.values():
        votes[o] += 1
    ranked = []
    for u, r in users.items():
        if any(w in u for w in bad_words):
            continue
        n_tags = min(len(r["tags"]), rw["max_friends_counted"])
        pts = (n_tags * rw["points_per_friend_tagged"] + (rw["points_for_vote"] if r["voted"] else 0)
               + min(r["likes"] * rw["points_per_comment_like"], rw["max_comment_like_points"]))
        if pts <= 0:
            continue
        if n_tags:
            detail = f"{n_tags} friend{'s' if n_tags != 1 else ''} tagged"
        elif r["likes"]:
            detail = f"{r['likes']} likes on comment"
        else:
            detail = "voted"
        ranked.append({"username": r["username"], "points": pts, "tags": n_tags, "comment_likes": r["likes"],
                       "detail": detail})
    ranked.sort(key=lambda r: (-r["points"], -r["tags"], r["username"].lower()))
    return votes, ranked


def comment_candidates(comments, winner, own, cfg, n=6):
    """Commenti della squadra vincente, dal piu' votato: GPT scegliera' il primo adatto."""
    own = own.lower()
    blocked = {u.lower().lstrip("@") for u in cfg["rewards"].get("blocked_users", [])}
    out = []
    for c in comments:
        u = (c.get("username") or "").lower()
        if not u or u == own or u in blocked or c.get("_option") != winner:
            continue
        text = MENTION.sub("", c.get("text") or "").strip()
        rest = re.sub(rf"(?<![A-Za-z0-9]){re.escape(winner)}(?![A-Za-z0-9])", "", text, flags=re.I)
        if len(re.sub(r"[^A-Za-z0-9]", "", rest)) < 8:  # solo la parola del voto o tag: non e' un'idea
            continue
        out.append({"username": c.get("username"), "text": text[:280], "likes": int(c.get("like_count") or 0)})
    out.sort(key=lambda x: (-x["likes"], len(x["text"])))
    return out[:n]


def collect(next_ep):
    cfg = config()
    st = state()
    prev = st["episodes"][-1]
    log(f"Raccolta dati per l'episodio {next_ep} (dopo l'episodio {prev['episode']})")

    new_token = ig.refresh_token()
    if new_token:
        with open(os.path.join(DATA, ".new_token"), "w") as f:
            f.write(new_token)
        log("  token Instagram rinnovato")

    acct = ig.me()
    own = acct.get("username") or cfg["instagram"]["username"]
    followers = int(acct.get("followers_count") or 0)

    media_id = prev.get("media_id")
    if not media_id:
        lr = ig.latest_reel()
        known = {e.get("media_id") for e in st["episodes"] if e.get("media_id")}
        if lr and lr["id"] in known:
            raise SystemExit(f"L'episodio {prev['episode']} non risulta pubblicato su Instagram.")
        media_id = lr["id"] if lr else None
        if media_id:
            prev["media_id"] = media_id
            prev["permalink"] = lr.get("permalink")
            log(f"  Reel dell'episodio {prev['episode']} trovato: {lr.get('permalink')}")
    if not media_id:
        raise SystemExit("Non trovo il Reel dell'ultimo episodio su Instagram: pubblicalo prima.")

    info = ig.media_info(media_id)
    likes = int(info.get("like_count") or 0)
    comms = ig.comments(media_id)
    log(f"  follower {followers}, like {likes}, commenti {len(comms)}")

    # follower nuovi da quando abbiamo misurato l'ultima volta
    base = st.get("last_followers")
    if base is None:
        base = prev.get("followers_at_publish")
    if base is None:
        base = cfg["money"].get("followers_at_start", 0)
    new_followers = max(0, followers - base)

    m = cfg["money"]
    earned = new_followers * m["per_follower"] + likes * m["per_like"]
    start_worth = prev.get("final_net_worth", 0) + earned

    options = prev.get("vote_options") or []
    votes, ranked = score(comms, options, cfg, own)
    winner = None
    if options:
        best = max(votes.values()) if votes else 0
        tied = [o for o, v in votes.items() if v == best]
        winner = tied[0] if len(tied) == 1 else None  # pareggio: decide la storia
    top = ranked[: cfg["rewards"]["top_n"]]
    total_votes = sum(votes.values())
    percentages = {o: round(100 * v / total_votes) if total_votes else 0 for o, v in votes.items()}
    candidates = comment_candidates(comms, winner, own, cfg) if winner else []
    losers = [o for o in options if o != winner]
    other_comments = []
    for o in losers:
        other_comments += comment_candidates(comms, o, own, cfg, n=3)
    other_comments.sort(key=lambda x: -x["likes"])

    out = {
        "episode": next_ep,
        "followers": followers,
        "new_followers": new_followers,
        "likes_last_episode": likes,
        "comments_last_episode": len(comms),
        "money_from_audience": earned,
        "net_worth_start": start_worth,
        "vote_options": options,
        "votes": votes,
        "percentages": percentages,
        "winner": winner,
        "comment_candidates": candidates,
        "other_comments": other_comments[:3],
        "supporters": top,
        "ranking": ranked[:20],
    }
    save_json(os.path.join(DATA, f"collect_ep{next_ep:02d}.json"), out)
    st["last_followers"] = followers
    for s in top:
        st.setdefault("featured", {}).setdefault(s["username"], []).append(next_ep)
    save_state(st)
    log(f"  voti {votes} -> {winner or 'pareggio'}; +${earned} dal pubblico; top: "
        + ", ".join("@" + s["username"] for s in top))
    return out


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else state()["episodes"][-1]["episode"] + 1
    collect(n)
