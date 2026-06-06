from __future__ import annotations

from bs4 import BeautifulSoup
import httpx


def scrape_lever(url: str) -> str:
    headers = {"User-Agent": "Mozilla/5.0"}
    with httpx.Client(timeout=20, follow_redirects=True, headers=headers) as client:
        response = client.get(url)
        response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    parts: list[str] = []

    h2 = soup.find("h2")
    if h2:
        parts.append(h2.get_text(" ", strip=True))

    if soup.title and soup.title.string:
        parts.append(soup.title.string.strip())

    for selector in ["span.posting-category", "span.location"]:
        node = soup.select_one(selector)
        if node:
            parts.append(node.get_text(" ", strip=True))

    for section in soup.select("div.section"):
        heading = section.find(["h3", "h4"])
        content = section.find("div", class_="content")
        if heading:
            parts.append(heading.get_text(" ", strip=True))
        if content:
            parts.append(content.get_text("\n", strip=True))

    return "\n\n".join(part for part in parts if part).strip()
