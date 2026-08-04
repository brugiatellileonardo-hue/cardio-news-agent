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
    "critical care": "Critical Care Reviews / ICU",
}

# --- Endpoint API reali (NON homepage) ---
PUBMED_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
TELEGRAM_URL_TEMPLATE = "https://api.telegram.org/bot{token}/sendMessage"


def get_all_recent_articles():
    query = 'Eur Heart J[ta] OR Circulation[ta] OR J Am Coll Cardiol[ta] OR "G Ital Cardiol (Rome)"[ta]'
    params = {
        "db": "pubmed",
       "term": f'{query} AND ("last 30 days"[PDat])',
        "retmax": 15,
        "sort": "most recent",
        "retmode": "json",
    }
    try:
        r = requests.get(PUBMED_ESEARCH, params=params, headers=HTTP_HEADERS, timeout=20)
        if r.status_code != 200:
            print(f"[PubMed Error] Codice HTTP {r.status_code}")
            return []
        id_list = r.json().get("esearchresult", {}).get("idlist", [])
        print(f"[PubMed] Trovati {len(id_list)} articoli.")
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
Act as an expert cardiologist and critical care specialist. Analyze this scientific entry:
Source: {source}
Title: {title}
Abstract: {abstract[:1800]}

If this article is NOT a major clinical trial, a new guideline, or a crucial clinical discovery, reply ONLY with the word: IGNORE.

If it is clinically relevant, provide a concise summary in ENGLISH formatted exactly as follows:
❤️ **[ARTICLE TITLE IN ENGLISH]**
🏛️ *Source:* {source}
🎯 *Clinical Relevance:* (Max 2 sentences)
📝 *Key Findings:* (max 4 short lines)
"""
    data = {
        "model": "llama3-8b-8192",
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


def main():
    print("--- AVVIO AGENTE CARDIO ---")
    ids = get_all_recent_articles()
    if not ids:
        print("Nessun articolo trovato.")
        return

    root = fetch_articles_xml(ids)
    if root is None:
        print("Impossibile decodificare i dettagli degli articoli.")
        return

    for art in root.findall(".//PubmedArticle"):
        pmid = art.findtext(".//PMID", default="")
        title = art.findtext(".//ArticleTitle", default="").strip()
        raw_journal = art.findtext(".//Journal/Title", default="Cardiology")

        abstract_parts = ["".join(ab.itertext()).strip() for ab in art.findall(".//Abstract/AbstractText")]
        abstract = " ".join(abstract_parts).strip()
        if not abstract:
            continue

        source_detected = identify_source(raw_journal)
        analysis = analyze_with_groq(title, abstract, source_detected)

        if "IGNORE" not in analysis:
            send_to_telegram(analysis, f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/")
            print(f"Notifica inoltrata per PMID: {pmid}")
        else:
            print(f"Ignorato (non rilevante): PMID {pmid}")
        time.sleep(1)

    print("--- MONITORAGGIO COMPLETATO ---")


if __name__ == "__main__":
    main()
