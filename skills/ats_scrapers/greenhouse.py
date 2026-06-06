from __future__ import annotations

from bs4 import BeautifulSoup
import httpx


def scrape_greenhouse(url: str) -> str:
    headers = {"User-Agent": "Mozilla/5.0"}
    with httpx.Client(timeout=20, follow_redirects=True, headers=headers) as client:
        response = client.get(url)
        response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    parts: list[str] = []
    h1 = soup.find("h1")
    if h1:
        parts.append(h1.get_text(" ", strip=True))

    location_div = soup.select_one("div.location")
    if location_div:
        parts.append(location_div.get_text(" ", strip=True))

    content_node = (
        soup.find("div", id="content")
        or soup.find("div", class_="job-description")
        or soup.find(attrs={"data-testid": "job-content"})
        or soup.find("main")
        or soup.find("article")
    )
    if content_node:
        parts.append(content_node.get_text("\n", strip=True))

    return "\n\n".join(part for part in parts if part).strip()
