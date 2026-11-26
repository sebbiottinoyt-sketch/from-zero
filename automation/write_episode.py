"""Scrive il copione del nuovo episodio con GPT e crea episodes/epNN/episode.json."""
import json
import os
import sys

from common import DATA, MOCK, ROOT, ask_json, config, ep_folder, load_json, log, save_json, state

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "day_label", "summary", "scenes", "vote", "supporters_narration", "instagram_caption"],
    "properties": {
        "title": {"type": "string"},
        "day_label": {"type": "string", "description": "e.g. DAY 2"},
        "summary": {"type": "string", "description": "2-3 sentences: what happened, for story memory"},
        "scenes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["image_prompt", "caption", "narration", "money", "camera"],
                "properties": {
                    "image_prompt": {"type": "string",
                                     "description": "What the picture shows: action, place, light, mood, framing"},
                    "caption": {"type": "string", "description": "On-screen text, max 8 words"},
                    "narration": {"type": "string", "description": "First-person voice-over, 12-22 words"},
                    "money": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["amount", "label", "at"],
                            "properties": {
                                "amount": {"type": "integer", "description": "positive = earned, negative = spent"},
                                "label": {"type": "string", "description": "1-2 words in CAPS, e.g. PHONE SOLD"},
                                "at": {"type": "number", "description": "0.2-0.9 = when in the scene"},
                            },
                        },
                    },
                    "camera": {"type": "string", "enum": ["push_in", "pull_out"]},
                },
            },
        },
        "vote": {
            "type": "object",
            "additionalProperties": False,
            "required": ["title", "options", "narration"],
            "properties": {
                "title": {"type": "string", "description": "short question, e.g. What's next?"},
                "options": {"type": "array", "items": {"type": "string"}},
                "narration": {"type": "string", "description": "asks to comment one of the two words"},
            },
        },
        "supporters_narration": {"type": "string",
                                 "description": "Thanks the top supporters (no names) and invites to tag friends"},
        "instagram_caption": {"type": "string"},
    },
}


def mock_script(n, col):
    return load_json(os.path.join(ROOT, "automation", "mock", "script.json"))


def write(n):
    cfg = config()
    st = state()
    col = load_json(os.path.join(DATA, f"collect_ep{n:02d}.json"))
    if not col:
        raise SystemExit(f"Manca data/collect_ep{n:02d}.json: esegui prima collect.py")
    bible = open(os.path.join(ROOT, "story", "bible.md"), encoding="utf-8").read()
    history = "\n".join(f"- Episode {e['episode']} ({e.get('day_label', '')}): {e['summary']}"
                        for e in st["episodes"])
    prev = st["episodes"][-1]
    if col["winner"]:
        vote_txt = (f"The audience voted {col['votes']}. The winning choice is {col['winner']}: "
                    f"this episode MUST follow {col['winner']}.")
    elif col["vote_options"]:
        vote_txt = (f"The vote between {col['vote_options']} was a tie ({col['votes']}): "
                    "choose the most interesting path and say the vote was a tie.")
    else:
        vote_txt = "There was no vote."
    n_scenes = cfg["episode"]["story_scenes"]
    user = f"""Write EPISODE {n} of the series.

STORY SO FAR:
{history}

AUDIENCE RESULTS SINCE THE LAST EPISODE:
- {vote_txt}
- New followers: {col['new_followers']}, likes on the last episode: {col['likes_last_episode']}.
- The audience gave him ${col['money_from_audience']:,} (followers x {cfg['money']['per_follower']} + likes x {cfg['money']['per_like']}).
- Net worth at the start of this episode: ${col['net_worth_start']:,} (end of last episode: ${prev.get('final_net_worth', 0):,}).

REQUIREMENTS:
- Exactly {n_scenes} story scenes, then the vote. Total narration for the story scenes: about {int(cfg['episode']['target_seconds'] * 1.8)} words.
- Scene 1 reacts to the audience result (the vote and the money they gave him).
- Use the money: show at least one way he earns or spends money; amounts realistic for his net worth.
- The last story scene leads to a new choice for the audience; vote options = two different 1-2 word ALL-CAPS paths.
- image_prompt: describe ONE clear picture with the main character (unless the scene is a close-up of an object), place, action, light, emotion. Vertical framing, character fully inside the frame, face clearly visible when he is in the shot. No text in the image.
- instagram_caption: hook line, net worth, the rules (every follower = ${cfg['money']['per_follower']}, every like = ${cfg['money']['per_like']}), the question with the two options, "Tag your friends: the top {cfg['rewards']['top_n']} supporters appear in the next episode", 4-6 hashtags including #fromzero.
"""
    if MOCK:
        script = mock_script(n, col)
    else:
        script = ask_json(cfg["openai"]["text_model"], bible, user, SCHEMA, "episode")
    script["scenes"] = script["scenes"][:n_scenes]
    save_json(os.path.join(DATA, f"script_ep{n:02d}.json"), script)

    folder = ep_folder(n)
    os.makedirs(folder, exist_ok=True)
    scenes = []
    for i, s in enumerate(script["scenes"]):
        scenes.append({
            "image": f"{i + 1:02d}.png",
            "image_prompt": s["image_prompt"],
            "caption": s["caption"],
            "narration": s["narration"],
            "camera": {"zoom": [1.04, 1.13] if s["camera"] == "push_in" else [1.14, 1.05]},
            "money": [{"amount": int(m["amount"]), "label": m["label"].upper()[:18],
                       "at": min(0.9, max(0.2, float(m["at"])))} for m in s["money"] if int(m["amount"]) != 0],
        })
    if col["supporters"]:
        scenes.append({
            "image": scenes[0]["image"], "blur": 14, "darken": 0.45,
            "supporters": [{"username": x["username"], "detail": x["detail"]} for x in col["supporters"]],
            "narration": script["supporters_narration"],
        })
    opts = [o.strip().upper()[:12] for o in script["vote"]["options"][:2]]
    scenes.append({
        "image": scenes[n_scenes - 1]["image"], "darken": 0.35, "duration": 4.5,
        "cta": {"title": script["vote"]["title"][:22], "options": opts},
        "narration": script["vote"]["narration"],
        "camera": {"zoom": [1.05, 1.1]},
    })
    v = cfg["voice"]
    ep = {"episode": n, "day_label": script["day_label"].upper()[:10], "title": script["title"],
          **v, "net_worth": col["net_worth_start"], "scenes": scenes,
          "instagram_caption": script["instagram_caption"]}
    save_json(os.path.join(folder, "episode.json"), ep)
    log(f"Copione episodio {n}: {script['title']} ({len(scenes)} scene, voto {opts})")
    return ep, script


if __name__ == "__main__":
    write(int(sys.argv[1]))
