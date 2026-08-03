"""
Agente news cardiologiche - FLUSSO FORZATO PER TEST
Invia un messaggio di avvio e inoltra i primi articoli trovati senza alcun filtro IA
"""
import os
import time
import xml.etree.ElementTree as ET
import requests

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

JOURNALS = {
    "European Heart Journal": "0195-668X",
    "Circulation": "0009-7322",
    "JACC": "0735-1097",
}

PUBMED_ESEARCH = "https://nih.gov"
PUBMED_EFETCH = "https://nih.gov"

def search_recent_ids(source_name, identifier):
    # Cerchiamo gli articoli degli ultimi 30 giorni per essere sicuri di trovare dati
    params = {"db": "pubmed", "term": f'"{identifier}"[Journal] AND ("last 30 days"[PDat])', "retmax": 1, "sort": "most recent", "retmode": "json"}
    try:
        r = requests.get(PUBMED_ESEARCH, params=params, timeout=20)
        return r.json().get("esearchresult", {}).get("idlist", [])
    except Exception as e:
        print(f"Errore PubMed {source_name}: {e}")
        return []

def fetch_articles_xml(pmids):
    if not pmids: return None
    params = {"db": "pubmed", "id": ",".join(pmids), "rettype": "abstract", "retmode": "xml"}
    try:
        resp = requests.get(PUBMED_EFETCH, params=params, timeout=20)
        return ET.fromstring(resp.content)
    except Exception:
        return None

def send_to_telegram(message):
    url = f"https://telegram.org{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        r = requests.post(url, json=payload, timeout=15)
        print(f"[Telegram Response] Status: {r.status_code}, Body: {r.text}")
    except Exception as e:
        print(f"Errore connessione Telegram: {e}")

def main():
    print("--- INIZIO TEST FORZATO ---")
    
    # 1. FORZATURA: Inviamo un messaggio immediato per capire se il canale Telegram funziona
    print("Spedizione messaggio di controllo su Telegram...")
    send_to_telegram("🎯 *TEST AGENTE CARDIO*: Se vedi questo messaggio, i codici Token e Chat ID su GitHub sono corretti al 100%!")

    for source_name, identifier in JOURNALS.items():
        print(f"Controllo rivista: {source_name}")
        ids = search_recent_ids(source_name, identifier)
        
        if not ids:
            print(f"Nessun articolo nell'ultimo mese per {source_name}")
            continue
            
        root = fetch_articles_xml(ids)
        if root is None: continue

        for art in root.findall(".//PubmedArticle"):
            pmid = art.findtext(".//PMID", default="")
            title = art.findtext(".//ArticleTitle", default="").strip()
            
            # 2. FORZATURA: Inviamo l'articolo direttamente a Telegram senza passare da Groq
            print(f"Forzatura invio articolo: {title}")
            test_message = f"📚 *Nuovo Articolo Trovato (Senza Filtro IA)*\n🏛️ *Source*: {source_name}\n❤️ *Title*: {title}\n🔗 https://nih.gov{pmid}/"
            send_to_telegram(test_message)

        time.sleep(1)
    print("--- FINE TEST FORZATO ---")

if __name__ == "__main__":
    main()
