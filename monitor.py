
import json
import re
import sys
import difflib
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

URLS = {
    "Heimspiele": "https://www.stpauli-fanladen.de/tickets/heimspiele-home-matches/",
    "Auswärtsfahrten": "https://www.stpauli-fanladen.de/tickets/die-naechsten-auswaertsfahrten/",
}

STATE_FILE = Path("state.json")
STATE_VERSION = 2

BOT_TOKEN = __import__("os").environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = __import__("os").environ["TELEGRAM_CHAT_ID"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; StPauliTicketMonitor/2.0)"
}

session = requests.Session()
session.headers.update(HEADERS)


def send_telegram(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    # Telegram erlaubt maximal 4096 Zeichen pro Nachricht.
    chunks = [message[i:i + 3900] for i in range(0, len(message), 3900)]

    for chunk in chunks:
        response = session.post(
            url,
            data={
                "chat_id": CHAT_ID,
                "text": chunk,
                "disable_web_page_preview": True,
            },
            timeout=30,
        )

        if not response.ok:
            raise RuntimeError(
                f"Telegram-Fehler {response.status_code}: {response.text}"
            )


def fetch_soup(url):
    response = session.get(url, timeout=30)
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


def get_content_root(soup):
    root = (
        soup.select_one("main")
        or soup.select_one(".entry-content")
        or soup.select_one(".site-main")
        or soup.body
    )

    if root is None:
        raise RuntimeError("Kein Seiteninhalt gefunden.")

    for element in root.select(
        "nav, header, footer, script, style, noscript, "
        ".cookie-notice, .cookie-banner, .cmplz-cookiebanner"
    ):
        element.decompose()

    return root


def clean_text(element):
    if element is None:
        return ""

    lines = []

    for line in element.get_text("\n").splitlines():
        line = " ".join(line.split())

        if line and (not lines or line != lines[-1]):
            lines.append(line)

    return "\n".join(lines)


def get_home_snapshot():
    soup = fetch_soup(URLS["Heimspiele"])
    root = get_content_root(soup)

    heading = None

    for candidate in root.find_all(re.compile(r"^h[1-6]$")):
        if "formulare heimspiele" in candidate.get_text(
            " ", strip=True
        ).lower():
            heading = candidate
            break

    if heading is None:
        raise RuntimeError(
            "Der Abschnitt 'Formulare Heimspiele' wurde nicht gefunden. "
            "Die Seite könnte geändert worden sein."
        )

    full_text = clean_text(root)
    heading_text = heading.get_text(" ", strip=True)

    position = full_text.lower().find(heading_text.lower())

    if position < 0:
        raise RuntimeError("Heimspiel-Abschnitt konnte nicht gelesen werden.")

    relevant_text = full_text[position:]

    links = []

    for anchor in heading.find_all_next("a", href=True):
        if root not in anchor.parents:
            continue

        href = urljoin(URLS["Heimspiele"], anchor["href"])
        label = anchor.get_text(" ", strip=True)

        if href.startswith("http"):
            links.append(f"{label} | {href}")

    content = relevant_text + "\n\nFORMULARLINKS:\n"
    content += "\n".join(sorted(set(links)))

    return {"content": content}


def get_article_detail(url):
    soup = fetch_soup(url)

    article = soup.select_one("article")
    root = article if article else get_content_root(soup)

    for element in root.select(
        "nav, header, footer, script, style, noscript, "
        ".cookie-notice, .cookie-banner"
    ):
        element.decompose()

    content = clean_text(root)

    if len(content) < 50:
        raise RuntimeError(f"Beitragsinhalt zu kurz: {url}")

    links = []

    for anchor in root.find_all("a", href=True):
        href = urljoin(url, anchor["href"])
        label = anchor.get_text(" ", strip=True)

        if href.startswith("http"):
            links.append(f"{label} | {href}")

    return content + "\n\nLINKS:\n" + "\n".join(sorted(set(links)))


def get_away_snapshot():
    soup = fetch_soup(URLS["Auswärtsfahrten"])
    root = get_content_root(soup)

    items = {}

    articles = root.find_all("article")

    if articles:
        for article in articles[:15]:
            title_node = article.select_one(
                ".entry-title, h2, h3"
            )

            if title_node is None:
                continue

            title = title_node.get_text(" ", strip=True)

            anchor = title_node.find("a", href=True)

            if anchor is None:
                anchor = article.find("a", href=True)

            if anchor is None:
                continue

            url = urljoin(URLS["Auswärtsfahrten"], anchor["href"])

            if urlparse(url).netloc != urlparse(
                URLS["Auswärtsfahrten"]
            ).netloc:
                continue

            date_node = article.select_one(
                "time, .entry-date, .posted-on"
            )
            date = clean_text(date_node)

            # Vollständigen Beitrag laden, nicht nur den Vorschautext.
            detail = get_article_detail(url)

            items[url] = {
                "title": title,
                "date": date,
                "content": detail,
            }

    else:
        # Fallback, falls die Website ihre Artikelstruktur ändert.
        for anchor in root.find_all("a", href=True):
            title = anchor.get_text(" ", strip=True)

            if not re.search(r"auswärts|auswärtsspiel", title, re.I):
                continue

            url = urljoin(URLS["Auswärtsfahrten"], anchor["href"])

            if urlparse(url).netloc != urlparse(
                URLS["Auswärtsfahrten"]
            ).netloc:
                continue

            detail = get_article_detail(url)

            items[url] = {
                "title": title,
                "date": "",
                "content": detail,
            }

    if not items:
        raise RuntimeError(
            "Keine Auswärtsfahrten gefunden. "
            "Die Seitenstruktur könnte sich geändert haben."
        )

    return items


def make_diff(old, new):
    diff = list(difflib.unified_diff(
        old.splitlines(),
        new.splitlines(),
        fromfile="Bisher",
        tofile="Neu",
        lineterm="",
    ))

    changes = [
        line for line in diff
        if line.startswith("+") or line.startswith("-")
        if not line.startswith("+++") and not line.startswith("---")
    ]

    return "\n".join(changes[:35])


def main():
    # Testmodus: Telegram testen, ohne Websites oder Status zu verändern.
    if "--test" in sys.argv:
        send_telegram(
            "⚫ FC ST. PAULI TICKET-TICKER\n\n"
            "✅ Test erfolgreich!\n"
            "Die Telegram-Verbindung funktioniert."
        )
        print("Telegram-Test erfolgreich.")
        return

    # Beide Seiten vollständig abrufen, bevor der Status verändert wird.
    new_state = {
        "version": STATE_VERSION,
        "home": get_home_snapshot(),
        "away": get_away_snapshot(),
    }

    old_state = {}

    if STATE_FILE.exists():
        old_state = json.loads(
            STATE_FILE.read_text(encoding="utf-8")
        )

    # Bei der Umstellung auf die neue Erkennung keinen Fehlalarm senden.
    if old_state.get("version") != STATE_VERSION:
        STATE_FILE.write_text(
            json.dumps(new_state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        send_telegram(
            "⚫ FC ST. PAULI TICKET-TICKER\n\n"
            "Die Überwachung wurde aktualisiert.\n"
            "Der neue Ausgangsstand wurde gespeichert.\n\n"
            "Ab jetzt werden relevante Änderungen gezielt gemeldet."
        )
        print("Neuer Ausgangsstand gespeichert.")
        return

    notifications = []

    # Heimspiele: Bestellformulare, Fristen und Links vergleichen.
    old_home = old_state.get("home", {}).get("content", "")
    new_home = new_state["home"]["content"]

    if old_home != new_home:
        changes = make_diff(old_home, new_home)

        notifications.append(
            "🏠 HEIMSPIELE – NEUE INFORMATION\n\n"
            f"{changes}\n\n"
            f"Zur Seite: {URLS['Heimspiele']}"
        )

    # Auswärtsfahrten: neue Beiträge und Änderungen erkennen.
    old_away = old_state.get("away", {})
    new_away = new_state["away"]

    for url, item in new_away.items():
        title = item["title"]
        old_item = old_away.get(url)

        if old_item is None:
            notifications.append(
                "🚌 NEUE AUSWÄRTSFAHRT!\n\n"
                f"{title}\n"
                f"{item['date']}\n\n"
                f"Zur Fahrt: {url}"
            )

        elif old_item.get("content") != item["content"]:
            changes = make_diff(
                old_item.get("content", ""),
                item["content"],
            )

            notifications.append(
                "🚌 UPDATE ZU EINER AUSWÄRTSFAHRT\n\n"
                f"{title}\n\n"
                f"{changes}\n\n"
                f"Zum Beitrag: {url}"
            )

    # Erst nach erfolgreichem Abruf und Vergleich speichern.
    STATE_FILE.write_text(
        json.dumps(new_state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if notifications:
        for notification in notifications:
            send_telegram(
                "⚫ FC ST. PAULI TICKET-TICKER\n\n" + notification
            )

        print(f"{len(notifications)} Änderungen gemeldet.")

    else:
        print("Keine relevanten Änderungen gefunden.")


if __name__ == "__main__":
    main()
