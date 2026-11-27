# From Zero – serie automatica

Ogni giorno GitHub fa tutto da solo:

1. **Legge Instagram** sull'ultimo episodio: follower, like, commenti.
2. **Conta i voti** nei commenti (una persona = un voto, vale l'ultimo commento).
3. **Calcola i soldi**: ogni follower nuovo = $10, ogni like = $1 → il contatore del nuovo episodio parte da lì.
4. **Fa la classifica dei fan** e mette i primi 3 con il loro @nome nella scena "TOP SUPPORTERS".
5. **Scrive il copione** con GPT seguendo la scelta più votata (`story/bible.md` = le regole della storia).
6. **Crea le immagini** con GPT Image usando il ragazzo dell'episodio 1 come riferimento.
7. **Anima il ragazzo** (respiro, testa, mani, occhi che sbattono) e monta il video con voce, contatore e barra episodi.
8. **Pubblica il Reel** (subito, oppure dopo il tuo OK).

## Il formato delle puntate (stile "interactive series")

- 22-28 inquadrature veloci (una frase ciascuna), parole a schermo una alla volta, con quelle chiave evidenziate.
- Barra in alto: EP, CASH (contatore a rulli), HOME, JOB. Elenco episodi all'inizio e alla fine.
- Apertura: risultato del voto con le percentuali e il **commento più votato della squadra vincente** (@nome, squadra, cuori):
  la puntata segue la sua idea.
- Riquadri (fatti, prezzi, obiettivi), messaggi che si scrivono da soli, avvisi rossi, timer con scadenza.
- Archi narrativi di 3-5 puntate (un problema ciascuno). L'ultima è il FINALE: chiude con un richiamo alla prima
  parte e dice "A NEW STORY STARTS TOMORROW". Soldi, casa e lavoro restano per sempre.
- Anche il commento più votato della squadra perdente può comparire ("@nome · 40 likes").
- Dialoghi con il nome di chi parla, messaggi inviati da Leo, etichette sui nuovi personaggi.
- Guardaroba: il volto di Leo resta sempre uguale, i vestiti cambiano con la sua vita. Quando ha i soldi e un motivo
  (colloquio, primo stipendio…) il voto finale diventa un voto di stile (es. SUIT o STREET): il pubblico lo veste.
  L'outfit attuale è in `data/state.json` -> `look`.
- Nota del produttore: in `config.json` -> `next_episode_note` puoi scrivere un'indicazione per la prossima puntata
  (es. "fai votare il pubblico su come vestirlo"); GPT la segue. Poi svuotala.
- Bilancio vero: ogni spesa, scommessa o perdita scala i soldi e resta per le puntate dopo.
- Finale: dilemma con due scelte e sottotitolo (il rischio di ognuna), "TOP FANS" e "TOMORROW 6PM · PART N".
- Le regole di regia per GPT sono in `story/director.md`; personaggio e mondo in `story/bible.md`.
- `config.json` -> `episode`: numero di inquadrature (`shots`), parole (`words`), immagini nuove massime
  (`max_new_images`, le altre sono primi piani delle stesse) e scritta finale (`footer`).

## Punti dei fan (si cambiano in `config.json` → `rewards`)

- 2 punti per ogni amico taggato nei commenti (contano al massimo 10 amici diversi a persona)
- 1 punto per chi vota
- 1 punto per ogni like ricevuto dal proprio commento (massimo 10)
- I primi 3 compaiono nel prossimo episodio. Non contano i tag a sé stessi o alla pagina.
- `blocked_users`: nomi da non mostrare mai; `blocked_words`: parole che escludono un nome.

Instagram **non dice chi ti segue o chi mette like** (solo i totali), quindi i premi si basano sui commenti.

---

## Configurazione (una volta sola, tutto da browser)

### 1. GitHub
1. Carica **tutto** il contenuto di questo zip nel repository `from-zero` (sostituisci i file vecchi; c'è anche la cartella nascosta `.github`).
2. Rendi il repository **pubblico**: Settings → General → in fondo "Change visibility" → Public.
   Serve perché Instagram scarica il video da un link pubblico. Le chiavi (punto 5) restano segrete.

### 2. Instagram
1. Nell'app: Impostazioni → Tipo di account e strumenti → **Passa a un account professionale** (Creator va bene).
2. Su **developers.facebook.com** → My Apps → **Create app** → scegli il caso d'uso per gestire contenuti/messaggi Instagram → **API setup with Instagram login**.
3. Aggiungi il tuo account Instagram e premi **Generate token**. Servono i permessi `instagram_business_basic`, `instagram_business_content_publish`, `instagram_business_manage_comments`.
4. Copia il token (dura 60 giorni, poi si rinnova da solo grazie al punto 4). L'app può restare in modalità sviluppo: è per il tuo account.

### 3. OpenAI
1. Su **platform.openai.com** → API keys → **Create new secret key** e copiala.
2. Billing → aggiungi credito (5–10 $ bastano per partire).
3. Se le immagini danno errore di "verification": Settings → Organization → **Verify organization**.

### 4. Token GitHub (per rinnovare da solo il token di Instagram)
github.com → foto profilo → Settings → Developer settings → Personal access tokens → **Fine-grained tokens** → Generate:
repository `from-zero`, permesso **Secrets: Read and write**. Copialo.

### 5. Le chiavi segrete
Nel repository: Settings → Secrets and variables → Actions → **New repository secret**, tre volte:
- `OPENAI_API_KEY` = chiave del punto 3
- `IG_TOKEN` = token del punto 2
- `GH_PAT` = token del punto 4

### 6. `config.json`
- `instagram.username`: il nome della pagina (es. `fromzero`)
- `auto_publish`: `false` = ti prepara il video e aspetta il tuo OK (consigliato all'inizio); `true` = pubblica da solo
- `min_hours_between_episodes`: (facoltativo) ore minime tra un episodio e l'altro, predefinito 20

### 7. Pubblica l'episodio 1
Pubblicalo a mano dal telefono (il video `output/ep01.mp4` e il testo in `output/ep01_caption.txt`):
il sistema lo trova da solo. Oppure Actions → **Pubblica episodio** → Run workflow → `1`.

---

## Uso di tutti i giorni

- Ogni giorno alle 16:00 (ora UTC) parte **Nuovo episodio (automatico)**. Per lanciarlo subito: Actions → Nuovo episodio → Run workflow.
- Con `auto_publish: false`: guarda il video in `output/epNN.mp4` dal telefono; se va bene, Actions → **Pubblica episodio** → Run workflow.
  Se non va bene, cancella la cartella `episodes/epNN`, il file `output/epNN.mp4` e i file `data/script_epNN.json` e rilancia (i dati Instagram già letti restano in `data/collect_epNN.json`).
- Finché un episodio non è pubblicato, il sistema non ne crea un altro.

## Costi indicativi
- Immagini: circa 0,04 $ l'una in qualità media → 5 immagini ≈ 0,20–0,30 $ a episodio.
- Copione e animazione: pochi centesimi.
- GitHub Actions: gratis per i repository pubblici.

## File utili
- `story/bible.md`: personaggio, stile e regole della storia (puoi modificarlo).
- `data/state.json`: riassunto degli episodi, soldi finali, link dei Reel.
- `data/collect_epNN.json`: voti, follower, like e classifica fan usati per l'episodio NN.
- `episodes/epNN/episode.json`: il copione montato (scene, testi, soldi, animazioni).

## Voce
Voce di un ragazzo (Kokoro, gratis, gira dentro GitHub). Le impostazioni sono in `config.json` → `voice`.
Le voci generate restano in `episodes/epNN/voice/` e vengono riusate finché il testo non cambia.

## Musica
Metti un file `assets/music.mp3` (senza copyright, per esempio dalla Audio Library di YouTube) e verrà aggiunto sotto la voce.

## Rimontare un episodio a mano
Actions → **Monta episodi** → Run workflow e scrivi il nome della cartella (es. `ep01`).
