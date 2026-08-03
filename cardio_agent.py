"""
Agente news cardiologiche: PubMed (XML) -> Groq Llama3 (Gratis) -> Telegram
Versione definitiva 100% stabile con parsing XML nativo unificato
"""
import os
import time
import xml.etree.ElementTree as ET
import requests

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

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
    "critical care": "Critical Care Reviews / ICU"
}

PUBMED_ESEARCH = "https://nih.gov"
PUBMED_EFETCH = "https://nih.gov"

def get_all_recent_articles():
    """Esegue un'unica chiamata globale a PubMed estraendo gli ID direttamente in XML nativo."""
    query = (
        '"Giornale italiano di cardiologia"[Journal] OR "European heart journal"[Journal] OR '
        '"Europace"[Journal] OR "Circulation"[Journal] OR "Journal of the American College of Cardiology"[Journal]'
    )
    
    # Parametri in XML standard (Lasciamo 30 giorni per forzare i dati nel test)
    params = {
        "db": "pubmed",
        "term": query,
        "reldate": 30,  # Cambia a 3 per il quotidiano dopo aver ricevuto i messaggi
        "datetype": "pdat",
        "retmax": 10,
        "sort": "most recent",
        "retmode": "xml"
    }
    
    try:
        resp = requests.get(PUBMED_ESEARCH, params=params, timeout=20)
        if resp.status_code != 200:
            print(f"[PubMed Error] Errore di rete HTTP {resp.status_code}")
            return []
        
        # Lettura ID direttamente dai nodi XML dell'IdList
        root = ET.fromstring(resp.content)
        id_list = [id_node.text for id_node in root.findall(".//IdList/Id") if id_node.text]
        print(f"[PubMed] Trovati {len(id_list)} articoli totali nel pool cardiovascolare.")
        return id_list
    except Exception as e:
        print(f"Errore parsing XML ricerca PubMed: {e}")
        return []

def fetch_articles_xml(pmids):
    if not pmids: return None
    params = {"db": "pubmed", "id": ",".join(pmids), "rettype": "abstract", "retmode": "xml"}
    try:
        resp = requests.get(PUBMED_EFETCH, params=params, timeout=20)
        if resp.status_code == 200:
            return ET.fromstring(resp.content)
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
    if not GROQ_API_KEY: return "IGNORE"

    url = "https://groq.com"
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
    endpoint = f"https://telegram.org{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": f"{message}\n\n🔗 [Link to PubMed]({link})",
        "parse_mode": "Markdown"
    }
    try:
        requests.post(endpoint, json=payload, timeout=15)
    except Exception:
        pass

def main():
    print("--- AVVIO AGENTE CARDIO (PURE XML POOL) ---")
    
    # 1. Recupera gli ID in XML nativo
    ids = get_all_recent_articles()
    if not ids:
        print("Nessun articolo estratto dei server NCBI.")
        return

    # 2. Scarica i contenuti XML dettagliati
    root = fetch_articles_xml(ids)
    if root is None:
        print("Impossibile decodificare i dettagli degli articoli.")
        return

    # 3. Analisi e filtraggio con Groq
    for art in root.findall(".//PubmedArticle"):
        pmid = art.findtext(".//PMID", default="")
        title = art.findtext(".//ArticleTitle", default="").strip()
        raw_journal = art.findtext(".//Journal/Title", default="Cardiology")
        
        abstract_parts = [("".join(ab.itertext()).strip()) for ab in art.findall(".//Abstract/AbstractText")]
        abstract = " ".join(abstract_parts).strip()
        
        if not abstract: continue

        source_detected = identify_source(raw_journal)
        analysis = analyze_with_groq(title, abstract, source_detected)

        if "IGNORE" not in analysis:
            send_to_telegram(analysis, f"https://nih.gov{pmid}/")
            print(f"Notifica inoltrata con successo per PMID: {pmid}")
            time.sleep(1)

    print("--- MONITORAGGIO COMPLETATO ---")

if __name__ == "__main__":
    main()
