"""
Agente news cardiologiche v2: PubMed -> Groq (triage con punteggio) -> Telegram

Modalità:
  python cardio_agent.py            -> run giornaliero (solo articoli con score >= soglia)
  python cardio_agent.py --digest   -> digest settimanale (articoli score 3+ non inviati)

File di stato (da committare dal workflow):
  sent_pmids.json    PMID già valutati (lista ordinata: si scartano i più vecchi)
  digest_queue.json  articoli valutati negli ultimi 14 giorni, per il digest
"""
import os
import re
import sys
import json
import time
import html
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import requests

# ------------------------------------------------------------------ config
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

MODEL = "openai/gpt-oss-20b"

SENT_LOG_PATH = "sent_pmids.json"
QUEUE_PATH = "digest_queue.json"
MAX_LOG_SIZE = 3000
QUEUE_KEEP_DAYS = 14

DAILY_THRESHOLD = 4        # score minimo per l'invio giornaliero
MAX_DAILY_CARDIO = 5       # massimo articoli cardio al giorno
DIGEST_MIN_SCORE = 3       # score minimo per entrare nel digest settimanale
MAX_DIGEST_ITEMS = 25
MAX_LLM_CALLS_CARDIO = 60  # tetto chiamate Groq per run (free tier)
MAX_LLM_CALLS_CC = 20
LLM_PAUSE = 3              # secondi tra chiamate Groq
SEARCH_DAYS = 3            # finestra di ricerca (la deduplica evita doppioni)

PUBMED_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
TELEGRAM_SEND = "https://api.telegram.org/bot{token}/sendMessage"

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# ------------------------------------------------------------------ query
EXCLUDED_TYPES = ('NOT (Editorial[pt] OR Comment[pt] OR Letter[pt] OR News[pt] '
                  'OR "Published Erratum"[pt] OR "Retraction of Publication"[pt])')

CARDIO_KEYWORDS = ('(cardiovascular[tiab] OR cardiac[tiab] OR heart[tiab] OR coronary[tiab] '
                   'OR myocardial[tiab] OR arrhythmia[tiab] OR "heart failure"[tiab])')

JACC_CIRC_SUBJOURNAL_ISSNS = [
    "2213-1779",  # JACC: Heart Failure
    "1936-8798",  # JACC: Cardiovascular Interventions
    "2405-500X",  # JACC: Clinical Electrophysiology
    "1941-3289",  # Circulation: Heart Failure
    "1941-7640",  # Circulation: Cardiovascular Interventions
]

# NUOVO: sottoriviste ESC + imaging (prima erano nel dizionario nomi ma non nella query)
ESC_IMAGING_JOURNALS = [
    "Eur Heart J Cardiovasc Imaging",
    "Eur Heart J Acute Cardiovasc Care",
    "Europace",
    "Eur J Heart Fail",
    "JACC Cardiovasc Imaging",
    "Circ Cardiovasc Imaging",
    "J Cardiovasc Magn Reson",
]

CC_QUERY = 'Crit Care Med[ta] OR Intensive Care Med[ta] OR "Crit Care"[ta] OR "0300-9572"[Journal]'


def cardio_queries():
    issn = " OR ".join(f'"{i}"[Journal]' for i in JACC_CIRC_SUBJOURNAL_ISSNS)
    subj = " OR ".join(f'"{j}"[ta]' for j in ESC_IMAGING_JOURNALS)
    return [
        'Eur Heart J[ta] OR Circulation[ta] OR J Am Coll Cardiol[ta] '
        f'OR "G Ital Cardiol (Rome)"[ta] OR "JAMA Cardiol"[ta] OR {issn}',
        subj,
        f'("N Engl J Med"[ta] OR "2766-5526"[Journal]) AND {CARDIO_KEYWORDS}',
        f'Lancet[ta] AND {CARDIO_KEYWORDS}',
    ]


# ------------------------------------------------------------------ prompt
TRIAGE_PROMPT = """You are triaging new medical literature for one specific reader.

READER: hospital cardiologist in an intensive cardiac care unit (Italy, follows ESC guidelines).
Core interests: cardiogenic shock and mechanical circulatory support; myocardial and pericardial
diseases (myocarditis, pericarditis, cardiomyopathies); advanced cardiac imaging (CMR, echo);
arrhythmias; acute and intensive cardiology.

SCORING (1-5):
5 = practice-changing: RCT, meta-analysis of RCTs, or guideline/consensus in a core interest;
    or a major RCT in any cardiovascular or critical care field.
4 = RCT outside core interests; large high-quality observational/registry study or official
    position paper in a core interest.
3 = solid observational study, registry or useful review in a core interest;
    large observational study in other fields.
2 = small or secondary analyses, mechanistic or technical papers with limited clinical impact.
1 = case reports, basic science, topics irrelevant to clinical cardiology.
Do not inflate: most articles should score 1-3.

ARTICLE
Source: {source}
PubMed publication types: {pubtypes}
Title: {title}
Abstract: {abstract}

Reply with ONLY a JSON object, no other text:
{{"score": <integer 1-5>,
"type": "RCT|meta-analysis|guideline|observational|review|case report|other",
"topic": "shock/MCS|myo-pericardial|imaging|arrhythmias|acute/ICU|heart failure|coronary|prevention|other",
"why": "<1-2 conversational sentences: why a cardiologist should care>",
"findings": "<3-4 short plain-spoken sentences on the actual results, no endpoint lists>",
"take_home": "<one sentence>"}}
Write why/findings/take_home in ENGLISH, conversational, like briefing a colleague over coffee.
Use only data present in the abstract."""

DIGEST_PROMPT = """You are a cardiologist briefing an intensive-care cardiology colleague (ESC guidelines).
Below are this week's highest-scored new papers, each with score, source, type and a one-line take-home.

{items}

Write 3-5 concise bullets on what could change clinical practice or is genuinely worth knowing.
Name the paper or trial briefly. Use only the information given; do not invent data.
If nothing is practice-changing, say so in one bullet.
Reply with ONLY a JSON object: {{"bullets": ["...", "..."]}}"""

TOPIC_ORDER = ["shock/MCS", "myo-pericardial", "imaging", "arrhythmias", "acute/ICU",
               "heart failure", "coronary", "prevention", "other"]


# ------------------------------------------------------------------ stato
def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[State] Errore lettura {path}, riparto da vuoto: {e}")
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def today_iso():
    return datetime.now(timezone.utc).date().isoformat()


def days_ago_iso(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()


# ------------------------------------------------------------------ PubMed
def get_with_retry(url, params, max_retries=3):
    """GET con retry su HTTP 429 (rate limit NCBI), backoff 2s, 4s, 6s."""
    r = None
    for attempt in range(max_retries):
        r = requests.get(url, params=params, headers=HTTP_HEADERS, timeout=30)
        if r.status_code == 429:
            wait = 2 * (attempt + 1)
            print(f"[Rate limit] 429, riprovo tra {wait}s ({attempt + 1}/{max_retries})")
            time.sleep(wait)
            continue
        return r
    return r


def search_pubmed(query, retmax=60):
    params = {
        "db": "pubmed",
        "term": f"({query}) {EXCLUDED_TYPES}",
        "datetype": "edat",      # data di ingresso in PubMed (PDat perdeva i fascicoli datati nel futuro)
        "reldate": SEARCH_DAYS,
        "retmax": retmax,
        "sort": "most recent",
        "retmode": "json",
    }
    try:
        r = get_with_retry(PUBMED_ESEARCH, params)
        if r.status_code != 200:
            print(f"[PubMed Error] HTTP {r.status_code}")
            return []
        ids = r.json().get("esearchresult", {}).get("idlist", [])
        print(f"[PubMed] {len(ids)} articoli per query: {query[:50]}...")
        time.sleep(0.5)
        return ids
    except Exception as e:
        print(f"[PubMed Exception] {e}")
        return []


def parse_article(art):
    title_el = art.find(".//ArticleTitle")
    abstract = " ".join(
        "".join(ab.itertext()).strip() for ab in art.findall(".//Abstract/AbstractText")
    ).strip()
    journal = (art.findtext(".//Journal/ISOAbbreviation")
               or art.findtext(".//Journal/Title") or "Journal").strip()
    return {
        "pmid": art.findtext(".//MedlineCitation/PMID", default="").strip(),
        "title": "".join(title_el.itertext()).strip() if title_el is not None else "",
        "abstract": abstract,
        "source": journal,
        "pubtypes": [pt.text for pt in art.findall(".//PublicationTypeList/PublicationType") if pt.text],
    }


def fetch_articles(pmids):
    out = []
    for i in range(0, len(pmids), 100):
        batch = pmids[i:i + 100]
        params = {"db": "pubmed", "id": ",".join(batch), "rettype": "abstract", "retmode": "xml"}
        try:
            r = get_with_retry(PUBMED_EFETCH, params)
            if r.status_code != 200:
                print(f"[PubMed EFetch Error] HTTP {r.status_code}")
                continue
            root = ET.fromstring(r.content)
        except Exception as e:
            print(f"[PubMed EFetch Exception] {e}")
            continue
        out.extend(parse_article(a) for a in root.findall(".//PubmedArticle"))
        time.sleep(0.5)
    return out


# ------------------------------------------------------------------ Groq
def parse_json(text):
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text or "", re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
    print("[Groq] Risposta non in JSON valido")
    return None


def groq_json(prompt):
    if not GROQ_API_KEY:
        print("[Groq] API key mancante.")
        return None
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    data = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    for _ in range(4):
        try:
            r = requests.post(GROQ_URL, json=data, headers=headers, timeout=60)
        except Exception as e:
            print(f"[Groq Exception] {e}")
            time.sleep(5)
            continue
        if r.status_code == 429:
            try:
                wait = min(float(r.headers.get("retry-after", 10)), 60)
            except ValueError:
                wait = 10
            print(f"[Groq] Rate limit, attendo {wait:.0f}s")
            time.sleep(wait)
            continue
        if r.status_code == 400 and "response_format" in data:
            # JSON mode rifiutata o output non conforme: riprovo senza
            print("[Groq] 400 in JSON mode, riprovo senza response_format")
            data.pop("response_format")
            continue
        if r.status_code != 200:
            print(f"[Groq Error] HTTP {r.status_code}: {r.text[:200]}")
            return None
        return parse_json(r.json()["choices"][0]["message"]["content"])
    return None


def triage(art):
    prompt = TRIAGE_PROMPT.format(
        source=art["source"],
        pubtypes=", ".join(art["pubtypes"]) or "n/a",
        title=art["title"],
        abstract=art["abstract"][:2500],
    )
    res = groq_json(prompt)
    if not res:
        return None
    try:
        score = max(1, min(5, int(res.get("score", 1))))
    except (TypeError, ValueError):
        score = 1
    return {
        "score": score,
        "type": str(res.get("type", "other")),
        "topic": str(res.get("topic", "other")),
        "why": str(res.get("why", "")),
        "findings": str(res.get("findings", "")),
        "take_home": str(res.get("take_home", "")),
    }


# ------------------------------------------------------------------ Telegram
def esc(s):
    return html.escape(str(s or ""), quote=False)


def pubmed_link(pmid):
    return f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"


def split_message(text, limit=4000):
    """Divide sui confini di paragrafo (Telegram: max 4096 caratteri per messaggio)."""
    chunks, current = [], ""
    for block in text.split("\n\n"):
        if current and len(current) + len(block) + 2 > limit:
            chunks.append(current)
            current = block
        else:
            current = f"{current}\n\n{block}" if current else block
    if current:
        chunks.append(current)
    return chunks


def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[Telegram] Token o Chat ID mancanti.")
        return False
    ok = True
    for chunk in split_message(text):
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            r = requests.post(TELEGRAM_SEND.format(token=TELEGRAM_BOT_TOKEN), json=payload, timeout=15)
            if r.status_code != 200:
                print(f"[Telegram Error] HTTP {r.status_code}: {r.text[:200]}")
                ok = False
        except Exception as e:
            print(f"[Telegram Exception] {e}")
            ok = False
        time.sleep(1)
    return ok


def format_item(item, header=None):
    parts = []
    if header:
        parts.append(header)
    parts.append(
        f"❤️ <b>{esc(item['title'])}</b>\n"
        f"🏛️ <i>{esc(item['source'])}</i> · {esc(item['type'])} · {'⭐' * item['score']}"
    )
    parts.append(f"🎯 <b>Why it matters:</b> {esc(item['why'])}")
    parts.append(f"📝 <b>What they found:</b> {esc(item['findings'])}")
    parts.append(f'🔗 <a href="{pubmed_link(item["pmid"])}">PubMed</a>')
    return "\n\n".join(parts)


def send_item(item, header=None):
    if send_telegram(format_item(item, header)):
        item["sent_daily"] = True
        print(f"[Telegram] Inviato PMID {item['pmid']} (score {item['score']})")


# ------------------------------------------------------------------ run giornaliero
def evaluate_new(pmids, seen, sent_log, max_calls, pool):
    pmids = [p for p in dict.fromkeys(pmids) if p not in seen]
    items, calls = [], 0
    for art in fetch_articles(pmids):
        pmid = art["pmid"]
        if not pmid or pmid in seen or not art["abstract"]:
            continue  # senza abstract: riprovato nei run successivi
        if calls >= max_calls:
            print(f"[{pool}] Tetto chiamate raggiunto: il resto al prossimo run.")
            break
        calls += 1
        result = triage(art)
        time.sleep(LLM_PAUSE)
        if result is None:
            continue  # errore temporaneo: riprovato al prossimo run
        item = {"pmid": pmid, "title": art["title"], "source": art["source"],
                **result, "pool": pool, "date": today_iso(), "sent_daily": False}
        items.append(item)
        seen.add(pmid)
        sent_log.append(pmid)
        print(f"[Triage] PMID {pmid}: score {item['score']} ({item['type']}, {item['topic']})")
    return items


def run_daily():
    print("--- AVVIO AGENTE CARDIO (giornaliero) ---")
    sent_log = load_json(SENT_LOG_PATH, [])
    seen = set(sent_log)
    queue = load_json(QUEUE_PATH, [])
    print(f"[State] {len(seen)} PMID già valutati, {len(queue)} in coda digest.")

    cardio_ids = [pid for q in cardio_queries() for pid in search_pubmed(q)]
    cardio = evaluate_new(cardio_ids, seen, sent_log, MAX_LLM_CALLS_CARDIO, "cardio")
    cc = evaluate_new(search_pubmed(CC_QUERY), seen, sent_log, MAX_LLM_CALLS_CC, "critical care")

    by_score = lambda i: i["score"]
    top = sorted((i for i in cardio if i["score"] >= DAILY_THRESHOLD),
                 key=by_score, reverse=True)[:MAX_DAILY_CARDIO]
    if top:
        for item in top:
            send_item(item)
    elif cardio:
        send_item(max(cardio, key=by_score),
                  header="📌 <b>Nessun articolo di rilievo oggi — il migliore della giornata:</b>")
    if cc:
        send_item(max(cc, key=by_score), header="🫀❄️ <b>Critical Care — Paper of the Day:</b>")

    cutoff = days_ago_iso(QUEUE_KEEP_DAYS)
    queue = [i for i in queue if i.get("date", "") >= cutoff] + cardio + cc
    save_json(QUEUE_PATH, queue)
    save_json(SENT_LOG_PATH, sent_log[-MAX_LOG_SIZE:])
    print(f"--- COMPLETATO: {len(cardio)} cardio + {len(cc)} CC valutati ---")


# ------------------------------------------------------------------ digest settimanale
def run_digest():
    print("--- AVVIO DIGEST SETTIMANALE ---")
    queue = load_json(QUEUE_PATH, [])
    cutoff = days_ago_iso(7)
    week = [i for i in queue if i.get("date", "") >= cutoff and not i.get("digested")]
    if not week:
        send_telegram("🗞️ <b>Digest settimanale</b>\n\nNessun articolo valutato questa settimana.")
        return

    by_score = lambda i: i["score"]
    highlights = sorted(week, key=by_score, reverse=True)[:20]
    items_txt = "\n".join(
        f"- [score {i['score']}] {i['title']} ({i['source']}, {i['type']}): {i['take_home']}"
        for i in highlights
    )
    synthesis = groq_json(DIGEST_PROMPT.format(items=items_txt)) or {}
    bullets = [b for b in synthesis.get("bullets", []) if isinstance(b, str)]

    n_sent = sum(1 for i in week if i.get("sent_daily"))
    parts = [f"🗞️ <b>Digest settimanale</b>\n{len(week)} articoli valutati, {n_sent} già inviati nei giorni scorsi"]
    if bullets:
        parts.append("🧭 <b>Cosa cambia nella pratica</b>\n" + "\n".join(f"• {esc(b)}" for b in bullets))

    unseen = sorted((i for i in week if not i.get("sent_daily") and i["score"] >= DIGEST_MIN_SCORE),
                    key=by_score, reverse=True)[:MAX_DIGEST_ITEMS]
    topics = sorted({i["topic"] for i in unseen},
                    key=lambda t: TOPIC_ORDER.index(t) if t in TOPIC_ORDER else len(TOPIC_ORDER))
    for topic in topics:
        lines = [
            f"• {'⭐' * i['score']} <a href=\"{pubmed_link(i['pmid'])}\">{esc(i['title'])}</a>"
            f" — <i>{esc(i['source'])}</i>\n  {esc(i['take_home'])}"
            for i in unseen if i["topic"] == topic
        ]
        parts.append(f"<b>{esc(topic)}</b>\n" + "\n".join(lines))
    if not unseen:
        parts.append("Nessun altro articolo con score ≥ 3 oltre a quelli già inviati.")

    if send_telegram("\n\n".join(parts)):
        for i in week:
            i["digested"] = True
        save_json(QUEUE_PATH, queue)
    print("--- DIGEST COMPLETATO ---")


if __name__ == "__main__":
    if "--digest" in sys.argv:
        run_digest()
    else:
        run_daily()
