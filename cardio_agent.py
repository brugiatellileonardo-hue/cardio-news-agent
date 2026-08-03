"""
Agente news cardiologiche: PubMed -> Groq Llama3 (Gratis) -> Telegram
Versione diagnostica allineata e protetta dai blocchi
"""
import os
import time
import xml.etree.ElementTree as ET
import requests

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

JOURNALS = {
    "Giornale Italiano di Cardiologia": "1827-6806",
    "European Heart Journal": "0195-668X",
    "EHJ Acute Cardiovascular Care": "2048-8734",
    "European Journal of Preventive Cardiology": "2047-4873",
    "European Journal of Heart Failure": "1388-9842",
    "Europace": "1099-5129",
    "EHJ Cardiovascular Imaging": "2047-2412",
    "Circulation": "0009-7322",
    "JACC": "0735-1097",
}

PUBMED_ESEARCH = "https://nih.gov"
PUBMED_EFETCH = "https://nih.gov"

def search_recent_ids(source_name, identifier):
    params = {"db": "pubmed", "term": f'"{identifier}"[Journal] AND ("last 3 days"[PDat])', "retmax": 2, "sort": "most recent", "retmode": "json"}
    try:
        r = requests.get(PUBMED_ESEARCH, params=params, timeout=20)
        return r.json().get("esearchresult", {}).get("idlist", [])
    except Exception as e:
        print(f"Errore ricerca PubMed {source_name}: {e}")
        return []

def fetch_articles_xml(pmids):
    if not pmids: return []
    params = {"db": "pubmed", "id": ",".join(pmids), "rettype": "abstract", "retmode": "xml"}
    try:
        resp = requests.get(PUBMED_EFETCH, params=params, timeout=20)
        return ET.fromstring(resp.content)
    except Exception as e:
        print(f"Errore XML PubMed: {e}")
        return None

def analyze_with_groq(title, abstract, source):
    if not GROQ_API_KEY:
        print("[DEBUG ERRORE] Chiave GROQ_API_KEY non rilevata dal sistema!")
        return "IGNORE"

    url = "https://groq.com"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    
    prompt = f"""
    Act as an expert cardiologist. Analyze this paper.
    Source: {source}
    Title: {title}
    Abstract: {abstract[:1500]}

    If it's NOT a major clinical trial, guideline, or critical discovery, reply ONLY with: IGNORE.
    If important, summarize in ENGLISH:
    ❤️ **[TITLE]**
    🏛️ *Source:* {source}
    🎯 *Clinical Relevance:* (2 sentences max)
    📝 *Key Findings:* (max 4 short lines)
    """
    
    data = {
        "model": "llama3-8b-8192",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1
    }
    
    try:
        response = requests.post(url, json=data, headers=headers, timeout=25)
        if response.status_code == 200:
            return response.json()["choices"][0]["message"]["content"].strip()
        print(f"[DEBUG GROQ HTTP ERROR] Code {response.status_code}: {response.text}")
        return "IGNORE"
    except Exception as e:
        print(f"Errore chiamata Groq: {e}")
        return "IGNORE"

def send_to_telegram(message, link):
    url = f"https://telegram.org{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": f"{message}\n\n🔗 [PubMed]({link})", "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=15)
    except Exception as e:
        print(f"Errore invio Telegram: {e}")

def main():
    print("--- AVVIO AGENTE GENERALE (CONNESSO A GROQ CLOUD) ---")
    
    all_sources = dict(JOURNALS)
    all_sources["Critical Care Reviews"] = "CC_REVIEW_SPECIAL"

    for source_name, identifier in all_sources.items():
        print(f"Analisi canale: {source_name}")
        ids = search_recent_ids(source_name, identifier)
        root = fetch_articles_xml(ids)
        if root is None: continue

        for art in root.findall(".//PubmedArticle"):
            pmid = art.findtext(".//PMID", default="")
            title = art.findtext(".//ArticleTitle", default="").strip()
            abstract_parts = [("".join(ab.itertext()).strip()) for ab in art.findall(".//Abstract/AbstractText")]
            abstract = " ".join(abstract_parts).strip()
            
            if not abstract: continue

            analysis = analyze_with_groq(title, abstract, source_name)

            if "IGNORE" not in analysis:
                send_to_telegram(analysis, f"https://nih.gov{pmid}/")
                print(f"Notifica inviata con successo su Telegram per: {title}")

        time.sleep(1)
    print("--- FINE MONITORAGGIO ---")

if __name__ == "__main__":
    main()
