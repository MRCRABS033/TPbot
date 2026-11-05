"""TibiaWiki en inglés (tibia.fandom.com): API de MediaWiki, usada para los datos de los bosses."""
API_URL = "https://tibia.fandom.com/api.php"
HEADERS = {"User-Agent": "Mozilla/5.0 (TPbot)"}


async def fetch_wikitext(session, page):
    """Wikitext de una página, o None si no existe. Los errores de conexión se propagan."""
    params = {"action": "parse", "page": page, "prop": "wikitext", "format": "json", "redirects": 1}
    async with session.get(API_URL, params=params, headers=HEADERS) as response:
        if response.status != 200:
            return None
        data = await response.json(content_type=None)
    return data.get("parse", {}).get("wikitext", {}).get("*")
