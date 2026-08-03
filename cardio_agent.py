"""
Agente news cardiologiche: PubMed -> Groq Llama3 (Gratis) -> Telegram
Versione con query di ricerca PubMed ottimizzata per risposte reali
"""
import os
import time
import xml.etree.ElementTree as ET
import requests

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

# Elenco ottimizzato: cerchiamo sia per ISSN sia per nome per massimizzare i risultati
JOURNALS = {
    "Giornale Italiano di Cardiologia": "Giornale italiano di cardiologia[Journal]",
    "European Heart Journal": "European heart journal[Journal]",
    "EHJ Acute Cardiovascular Care": "European heart journal acute cardiovascular care[Journal]",
    "European Journal of Preventive Cardiology": "European journal of preventive cardiology[Journal]",
    "European Journal of Heart Failure": "European journal of heart failure[Journal]",
    "Europace": "Europace[Journal]",
    "EHJ Cardiovascular Imaging": "European heart journal cardiovascular Imaging[Journal]",
    "Circulation": "Circulation[Journal]",
    "JACC": "Journal of the American College of Cardiology[Journal]",
}

PUBMED_ESEARCH = "https://nih.gov"
PUBMED_EFETCH = "https://nih.gov"

def search_recent_ids(source_name, query_term):
    # Usiamo reldate=30 (ultimi 30 giorni) e datetype=pdat per forzare PubMed a rispondere
    params = {
        "db": "pubmed",
        "term": query_term,
        "reldate": 30,       # Cambia a 3 per il monitoraggio quotidiano dopo il test
        "datetype": "pdat",
        "retmax": 5,
        "sort": "most recent",
        "retmode": "json"
    }
    try:
        r = requests.get(PUBMED_ESEARCH, params=params, timeout=20)
        if r.status_code == 200:
            id_list = r.json().get("esearchresult", {}).get("idlist", [])
            print(f"[PubMed Search] {source_name}: trovati {len(id_list)} articoli.")
            return id_list
        return []
    except Exception as e:
        print(f"Errore ricerca PubMed {source_name}: {e}")
        return []

def fetch_articles_xml(pmids):
    if not pmids: return None
    params = {"db": "pubmed", "id": ",".join(pmids), "rettype": "abstract", "retmode": "xml"}
    try:
        resp = requests.get(PUBMED_EFETCH, params=params, timeout=20)
        if resp.status_code == 200:
            return ET.fromstring(resp.content)
        return None
    except Exception:
        return None

def analyze_with_groq(title, abstract, source):
    if not GROQ_API_KEY:
        return "IGNORE"

    url = "https://groq.com"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    
    prompt = f"""
    Act as an expert cardiologist and critical care specialist. Analyze this scientific entry:
    Source: {source}
    Title: {title}
    Abstract: {abstract[:2000]}

    If this article is NOT a major clinical trial, a new guideline, or a crucial clinical discovery, reply ONLY with the word: IGNORE.
    
    If it is clinically relevant, provide a concise summary in ENGLISH formatted exactly as follows:
    ❤️ **[ARTICLE TITLE IN ENGLISH]**
    🏛️ *Source:* {source}
    🎯 *Clinical Relevance:* (Max 2 sentences explaining why a practicing physician needs to know this)
    📝 *Key Findings:* (Summarize main endpoints or results in max 4 short lines)
    """
    
    data = {
        "model": "llama3-8b-8192",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1
    }
    
    try:
        response = requests.post(url, json=data, headers=headers, timeout=25)
        if response.status_code == 200:
            return response.json()["choices"]["message"]["content"].strip()
        return "IGNORE"
    except Exception:
        return "IGNORE"

def send_to_telegram(message, link):
    base_url = "https://api.telegram.org"
    endpoint = f"{base_url}/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": f"{message}\n\n🔗 [Link to PubMed]({link})",
        "parse_mode": "Markdown"
    }
    try:
        r = requests.post(endpoint, json=payload, timeout=15)
        if r.status_code != 200:
            print(f"[Telegram Error] Status {r.status_code}")
    except Exception as e:
        print(f"Errore Telegram: {e}")

def main():
    print("--- AVVIO AGENTE CARDIO (QUERY OTTIMIZZATA) ---")
    
    all_sources = dict(JOURNALS)
    # Gestione per Critical Care Reviews basata su parole chiave stabili
    all_sources["Critical Care Reviews"] = "(critical care[Journal]) AND (trial[Title/Abstract] OR guideline[Title/Abstract])"

    for source_name, query_term in all_sources.items():
        ids = search_recent_ids(source_name, query_term)
        
        if not ids:
            continue
            
        root = fetch_articles_xml(ids)
        if root is None: continue

        for art in root.findall(".//PubmedArticle"):
            pmid = art.findtext(".//PMID", default="")
            title = art.findtext(".//ArticleTitle", default="").strip()
            abstract_parts = [("".join(ab.itertext()).strip()) for ab in art.findall(".//Abstract/AbstractText")]
            abstract = " ".join(abstract_parts).strip()
            
            if not abstract: continue

            # Chiamata all'IA per filtrare solo i trial reali
            analysis = analyze_with_groq(title, abstract, source_name)

            if "IGNORE" not in analysis:
                send_to_telegram(analysis, f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/")
                print(f"Inviata notifica per: {title}")

        time.sleep(1)
    print("--- MONITORAGGIO COMPLETATO ---")

if __name__ == "__main__":
    main()
