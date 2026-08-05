"""
Agente news cardiologiche: PubMed -> Groq Llama3 (gratis) -> Telegram
"""
import os
import time
import xml.etree.ElementTree as ET
import requests

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

JOURNAL_NAMES = {
    "giornale italiano di cardiologia": "Giornale Italiano di Cardiologia",
    "european heart journal": "European Heart Journal",
    "acute cardiovascular care": "EHJ Acute Cardiovascular Care",
    "preventive cardiology": "European Journal of Preventive Cardiology",
    "heart failure": "European Journal of Heart Failure",
    "europace": "Europace",
    "cardiovascular imaging": "EHJ Cardiovascular Imaging",
    "circulation": "Circulation",
    "american college of cardiology": "JACC / ACC",
    "jama cardiology": "JAMA Cardiology",
    "new england journal": "NEJM",
    "critical care": "Critical Care Reviews / ICU",
}

# --- Endpoint API reali (NON homepage) ---
PUBMED_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
TELEGRAM_URL_TEMPLATE = "https://api.telegram.org/bot{token}/sendMessage"


def get_all_recent_articles():
    query = (
        'Eur Heart J[ta] OR Circulation[ta] OR J Am Coll Cardiol[ta] '
        'OR "G Ital Cardiol (Rome)"[ta] OR "JAMA Cardiol"[ta]'
    )
    return _search_pubmed(query)


def get_nejm_cardio_articles():
    # NEJM non ha una sezione cardio indicizzata separatamente: filtriamo per parole chiave nel titolo/abstract
    query = (
        '"N Engl J Med"[ta] AND (cardiovascular[tiab] OR cardiac[tiab] OR heart[tiab] '
        'OR coronary[tiab] OR myocardial[tiab] OR arrhythmia[tiab] OR "heart failure"[tiab])'
    )
    return _search_pubmed(query)


def get_critical_care_articles():
    query = 'Crit Care Med[ta] OR Intensive Care Med[ta] OR "Crit Care"[ta]'
    return _search_pubmed(query)


def _search_pubmed(query, days=30, retmax=15):
    params = {
        "db": "pubmed",
        "term": f'{query} AND ("last {days} days"[PDat])',
        "retmax": retmax,
        "sort": "most recent",
        "retmode": "json",
    }
    try:
        r = requests.get(PUBMED_ESEARCH, params=params, headers=HTTP_HEADERS, timeout=20)
        if r.status_code != 200:
            print(f"[PubMed Error] Codice HTTP {r.status_code}")
            return []
        id_list = r.json().get("esearchresult", {}).get("idlist", [])
        print(f"[PubMed] Trovati {len(id_list)} articoli per query: {query[:40]}...")
        return id_list
    except Exception as e:
        print(f"Errore recupero ID da PubMed: {e}")
        return []


def fetch_articles_xml(pmids):
    if not pmids:
        return None
    params = {"db": "pubmed", "id": ",".join(pmids), "rettype": "abstract", "retmode": "xml"}
    try:
        resp = requests.get(PUBMED_EFETCH, params=params, headers=HTTP_HEADERS, timeout=20)
        if resp.status_code == 200:
            return ET.fromstring(resp.content)
        print(f"[PubMed EFetch Error] Codice HTTP {resp.status_code}")
        return None
    except Exception as e:
        print(f"Errore recupero dettagli XML: {e}")
        return None


def identify_source(journal_title):
    title_lower = journal_title.lower()
    for key, output_name in JOURNAL_NAMES.items():
        if key in title_lower:
            return output_name
    return "Cardiology Journal"


def analyze_with_groq(title, abstract, source):
    if not GROQ_API_KEY:
        print("[Groq] API key mancante.")
        return "IGNORE"

    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    prompt = f"""
You are a cardiologist colleague giving a quick, spoken-style briefing to another cardiologist over coffee — not writing a formal abstract summary. Think NotebookLM podcast style: natural, conversational, gets to the point, explains *why it matters* before *what it found*.

Source: {source}
Title: {title}
Abstract: {abstract[:1800]}

If this article is NOT a major clinical trial, a new guideline, or a crucial clinical discovery, reply ONLY with the word: IGNORE.

If it is clinically relevant, write in ENGLISH, conversational tone, formatted exactly as follows (keep each section short and natural-sounding, like you're explaining it out loud — avoid dry academic phrasing):
❤️ **[ARTICLE TITLE IN ENGLISH]**
🏛️ *Source:* {source}
🎯 *Why it matters:* (1-2 conversational sentences — what changes for practice, or why a cardiologist should care)
📝 *What they found:* (3-4 short, plain-spoken sentences on the actual results — as if summarizing to a colleague, not listing endpoints)
"""
    data = {
        "model": "openai/gpt-oss-20b",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
    }
    try:
        response = requests.post(GROQ_URL, json=data, headers=headers, timeout=25)
        if response.status_code == 200:
            return response.json()["choices"][0]["message"]["content"].strip()
        print(f"[Groq Error] Codice HTTP {response.status_code}: {response.text[:200]}")
        return "IGNORE"
    except Exception as e:
        print(f"[Groq Exception] {e}")
        return "IGNORE"


def summarize_no_filter(title, abstract, source):
    """Come analyze_with_groq ma senza filtro di rilevanza: riassume sempre."""
    if not GROQ_API_KEY:
        return None

    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    prompt = f"""
You are a cardiologist colleague giving a quick, spoken-style briefing over coffee — NotebookLM podcast style: natural, conversational, not a dry academic abstract. Summarize this article for a fellow cardiologist regardless of how groundbreaking it is:

Source: {source}
Title: {title}
Abstract: {abstract[:1800]}

Write in ENGLISH, conversational tone, formatted exactly as follows:
📄 **[ARTICLE TITLE IN ENGLISH]**
🏛️ *Source:* {source}
📝 *The gist:* (3-4 short, plain-spoken sentences — as if explaining it out loud to a colleague, not listing endpoints)
"""
    data = {
        "model": "openai/gpt-oss-20b",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
    }
    try:
        response = requests.post(GROQ_URL, json=data, headers=headers, timeout=25)
        if response.status_code == 200:
            return response.json()["choices"][0]["message"]["content"].strip()
        print(f"[Groq Error - fallback] Codice HTTP {response.status_code}: {response.text[:200]}")
        return None
    except Exception as e:
        print(f"[Groq Exception - fallback] {e}")
        return None


def send_to_telegram(message, link):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[Telegram] Token o Chat ID mancanti.")
        return
    endpoint = TELEGRAM_URL_TEMPLATE.format(token=TELEGRAM_BOT_TOKEN)
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": f"{message}\n\n🔗 [Link to PubMed]({link})",
        "parse_mode": "Markdown",
    }
    try:
        r = requests.post(endpoint, json=payload, timeout=15)
        if r.status_code != 200:
            print(f"[Telegram Error] Codice HTTP {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"[Telegram Exception] {e}")


def process_articles(root, apply_filter):
    """Estrae gli articoli dall'XML. Se apply_filter=True invia solo i rilevanti (filtro Groq).
    Se apply_filter=False invia sempre un riassunto del primo articolo trovato (paper of the day)."""
    first_article = None
    notifications_sent = 0

    for art in root.findall(".//PubmedArticle"):
        pmid = art.findtext(".//PMID", default="")
        title = art.findtext(".//ArticleTitle", default="").strip()
        raw_journal = art.findtext(".//Journal/Title", default="Cardiology")

        abstract_parts = ["".join(ab.itertext()).strip() for ab in art.findall(".//Abstract/AbstractText")]
        abstract = " ".join(abstract_parts).strip()
        if not abstract:
            continue

        source_detected = identify_source(raw_journal)

        if first_article is None:
            first_article = {"pmid": pmid, "title": title, "abstract": abstract, "source": source_detected}

        if apply_filter:
            analysis = analyze_with_groq(title, abstract, source_detected)
            if "IGNORE" not in analysis:
                send_to_telegram(analysis, f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/")
                print(f"Notifica inoltrata per PMID: {pmid}")
                notifications_sent += 1
            else:
                print(f"Ignorato (non rilevante): PMID {pmid}")
            time.sleep(1)

    return first_article, notifications_sent


def main():
    print("--- AVVIO AGENTE CARDIO ---")

    # --- Pool 1: cardiologia (incl. JAMA Cardiology), con filtro di rilevanza ---
    ids = get_all_recent_articles() + get_nejm_cardio_articles()
    if ids:
        root = fetch_articles_xml(ids)
        if root is not None:
            first_cardio, notifications_sent = process_articles(root, apply_filter=True)
            if notifications_sent == 0 and first_cardio is not None:
                print("Nessun trial cardio rilevante: invio il paper of the day cardio come fallback.")
                summary = summarize_no_filter(first_cardio["title"], first_cardio["abstract"], first_cardio["source"])
                if summary:
                    header = "📌 *Nessun trial cardio di rilievo oggi — paper of the day:*\n\n"
                    link = f"https://pubmed.ncbi.nlm.nih.gov/{first_cardio['pmid']}/"
                    send_to_telegram(header + summary, link)
        else:
            print("Impossibile decodificare i dettagli degli articoli cardio.")
    else:
        print("Nessun articolo cardio trovato.")

    # --- Pool 2: critical care, SEMPRE paper of the day (indipendente dal pool cardio) ---
    cc_ids = get_critical_care_articles()
    if cc_ids:
        cc_root = fetch_articles_xml(cc_ids)
        if cc_root is not None:
            first_cc, _ = process_articles(cc_root, apply_filter=False)
            if first_cc is not None:
                summary = summarize_no_filter(first_cc["title"], first_cc["abstract"], "Critical Care Reviews")
                if summary:
                    header = "🫀❄️ *Critical Care — Paper of the Day:*\n\n"
                    link = f"https://pubmed.ncbi.nlm.nih.gov/{first_cc['pmid']}/"
                    send_to_telegram(header + summary, link)
                    print(f"Paper of the day critical care inviato: PMID {first_cc['pmid']}")
        else:
            print("Impossibile decodificare i dettagli degli articoli critical care.")
    else:
        print("Nessun articolo critical care trovato.")

    print("--- MONITORAGGIO COMPLETATO ---")


if __name__ == "__main__":
    main()
