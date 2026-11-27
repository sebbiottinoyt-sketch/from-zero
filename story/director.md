# Director's rules (format of every episode)

The series is an INTERACTIVE story. We only give the spark: the audience decides.
The winning option decides the direction; the most liked comment of the winning team decides HOW it happens.

## Rhythm
- 22–28 SHOTS, total narration about 170–200 words (about 65–75 seconds).
- One shot = one short sentence (4–12 words), about 2–3 seconds. Never two ideas in one shot.
- Change the picture every shot. Mix framings: wide, medium, closeup_face, closeup_hands, detail (an object: a note, a phone screen, cash, a ticket, a key).
- Reuse pictures smartly: a closeup of an image already made is free (reuse_shot). Make at most {max_new_images} NEW pictures.

## Voice
- First person, Leo speaks ("I"). Short punchy sentences. Simple English. Present-tense energy.
- Mark 1–2 key words per sentence with *asterisks* (they appear highlighted on screen): money, names, twists, decisions.

## Structure (always)
1. Shot 1: hook that recaps where we left off, in one line ("Last night you told me to…").
2. Shot 2: the vote result ("82% of you said HONEST.").
3. Shot 3: the most liked comment of the winning side, read as an idea Leo follows ("@user said: …. So that's what I did."). If there is no comment, skip it.
4. Middle: follow the winning choice and the comment's idea literally, show consequences (good AND bad), money moving.
5. A TWIST around 60–70%: a new character, a message, a deadline, a hidden detail. Raise the stakes.
6. A share line near the end: "Send this to the friend who…" (as a card).
7. Last shot (the vote): a dilemma with no obvious right answer. It must test intelligence or ethics: risk vs safety, money vs honesty, loyalty vs opportunity, trust vs suspicion. Two options, 1–2 words each, ALL CAPS, each with a short subtitle that shows its risk ("$500 tonight, but whose phones are they?").

## Story arcs
- The long story (from zero to richest man) is told in ARCS of 3–5 parts. Each arc is one problem/dilemma
  with its own characters (e.g. "The Black Car", "The Lottery Ticket"). Money, home and job carry over forever.
- Part 1 of an arc plants a detail (an object, a note, a name) that pays off in the finale.
- The FINALE resolves the arc with a callback to that detail and a moral twist; the last vote asks what the
  audience WOULD have done, and says a new story starts tomorrow.

## On-screen elements (use them, they make the format)
- card: label + value (facts, prices, goals, real-world rules, odds). Colors: green = money/good, yellow = info, red = danger, white = neutral.
- dm: a message that types itself on screen (from a character). Great for twists and threats.
- dm_out: a message Leo sends ("YOU → @name").
- say: a line of dialogue spoken by another character, with their name as header (e.g. "THE OLD LADY").
- tag: a name label on a new character, 2–4 words ("DAVIDE · THE NEPHEW").
- alert: big red words (2–4 words) for shocking details ("HE KNEW MY NAME").
- timer: a countdown when there is a deadline (set with a label like "OFFICE CLOSES IN"; jump when story time passes; turn red when urgent; hide when over).
- stats: HOME and JOB in the top bar (short labels, e.g. "NO", "SHELTER", "ROOM", "CAR WASH", "PHONE FIX"). Change them only when the story changes them.

## Leo's look (the audience dresses him)
- His face and hair never change. His clothes follow his life: street clothes while he is broke; as money, job and
  home improve he becomes more presentable.
- The AUDIENCE chooses his clothes: when he has enough cash for new clothes (about $150+) and the story gives a reason
  (a job interview, a meeting, a date, a first paycheck), make the final vote a STYLE vote: two outfits with a short
  name ("SUIT" / "STREET", "SMART" / "SPORTY") and a subtitle with price and vibe. At most one style vote per arc.
- In the episode after a style vote: Leo buys the winning outfit (money event with the real price), set new_look to
  a precise visual description of it (colors, pieces, shoes, accessories) and mark wears_new=true from the shot
  where he wears it. Keep it consistent afterwards. If the story makes him lose everything, he can go back to worse clothes.

## Money (strict ledger)
- The top bar shows CASH. Every amount Leo earns, spends, invests, bets or loses is a money event (+ or −) in the shot where it happens.
- He can never spend more than he has at that moment. Losses stay lost: the next episode starts from what is left plus the audience money.
- Amounts must be realistic for his situation and net worth.

## Tone
- Suspense in every episode: something can go wrong at any moment. End on a cliffhanger.
- Smart, a bit street-wise, emotional but never cheesy. Small wins, real setbacks.
- Family-friendly: no violence shown, nothing sexual, no hate. Crime and scams can appear as temptations or threats, never as glamorous.
- No real brands or real people. Real-world facts (laws, odds, prices) are welcome on cards if they are generally true.
