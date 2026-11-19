
import os
import json
import hashlib
import difflib
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

URLS = {
    "Heimspiele": "https://www.stpauli-fanladen.de/tickets/heimspiele-home-matches/",
    "Auswärtsfahrten": "https://www.stpauli-fanladen.de/tickets/die-naechsten-auswaertsfahrten/",
}

STATE_FILE = Path("state.json")
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; StPauliTicketMonitor/1.0)"
}


def get_page_content(url):
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    root = soup.select_one("main") or soup.select_one(".entry-content") or soup.body

    if root is None:
        raise ValueError(f"Kein Seiteninhalt gefunden: {url}")

    for element in root.select(
        "nav, header, footer, script, style, noscript, svg, "
        ".cookie-notice, .cookie-banner, .cmplz-cookiebanner"
    ):
        element.decompose()

    lines = []

    for line in root.get_text("\n").splitlines():
        line = " ".join(line.split())
        if line and (not lines or line != lines[-1]):
            lines.append(line)

    for link in root.find_all("a", href=True):
        label = " ".join(link.get_text(" ", strip=True).split())
        href = urljoin(url, link["href"])

        if label and href.startswith("http"):
            lines.append(f"LINK: {label} | {href}")

    return "\n".join(lines)


def send_telegram(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    response = requests.post(
        url,
        data={
            "chat_id": CHAT_ID,
            "text": message[:4000],
            "disable_web_page_preview": True,
        },
        timeout=30,
    )
    response.raise_for_status()

    result = response.json()
    if not result.get("ok"):
        raise RuntimeError(f"Telegram-Fehler: {result}")


def main():
    state = json.loads(STATE_FILE.read_text(encoding="utf-8")) \
        if STATE_FILE.exists() else {}

    first_run = not state
    new_state = {}

    # Erst beide Seiten erfolgreich abrufen, dann vergleichen.
    for name, url in URLS.items():
        content = get_page_content(url)
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()

        new_state[name] = {
            "hash": digest,
            "content": content,
        }

    changes = []

    if not first_run:
        for name, url in URLS.items():
            old = state.get(name, {}).get("content", "")
            new = new_state[name]["content"]

            if old != new:
                diff = list(difflib.unified_diff(
                    old.splitlines(),
                    new.splitlines(),
                    fromfile="Bisher",
                    tofile="Neu",
                    lineterm="",
                ))

                details = "\n".join(diff[2:]) or "Inhalt wurde verändert."

                changes.append(
                    f"🔔 Änderung bei: {name}\n"
                    f"Quelle: {url}\n\n"
                    f"{details[:2500]}"
                )

    if first_run:
        send_telegram(
            "⚫ FC ST. PAULI TICKET-TICKER\n\n"
            "Der Monitor ist eingerichtet!\n"
            "Beide Seiten wurden erstmalig eingelesen.\n\n"
            "Ab jetzt melde ich neue oder geänderte Informationen."
        )

    elif changes:
        for change in changes:
            send_telegram(
                "⚫ FC ST. PAULI TICKET-TICKER\n\n" + change
            )

    # Erst nach erfolgreicher Verarbeitung speichern.
    STATE_FILE.write_text(
        json.dumps(new_state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"Prüfung abgeschlossen. Änderungen: {len(changes)}")


if __name__ == "__main__":
    main()
