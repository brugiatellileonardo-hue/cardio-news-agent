"""
Script di Test Diretto per Connessione Telegram
"""
import os
import requests

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

def main():
    print("--- INIZIO VERIFICA CONNESSIONE TELEGRAM ---")
    print(f"Token rilevato (lunghezza): {len(TELEGRAM_BOT_TOKEN)} caratteri")
    print(f"Chat ID rilevato: {TELEGRAM_CHAT_ID}")

    url = f"https://telegram.org{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": "🔌 *CONNESSO!* L'agente cardio riesce a comunicare con il tuo smartphone. Il ponte di Telegram è configurato correttamente!",
        "parse_mode": "Markdown"
    }

    try:
        response = requests.post(url, json=payload, timeout=15)
        print(f"Risposta ufficiale del server Telegram (Status Code): {response.status_code}")
        print(f"Contenuto risposta: {response.text}")
    except Exception as e:
        print(f"Errore di rete durante la connessione a Telegram: {e}")
        
    print("--- FINE VERIFICA ---")

if __name__ == "__main__":
    main()
