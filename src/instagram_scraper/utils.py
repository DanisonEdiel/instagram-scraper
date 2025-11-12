import re
from urllib.parse import urlparse


_USERNAME_RE = re.compile(r"^[A-Za-z0-9._]+$")
_INVALID_FIRST_SEGMENTS = {"p", "reels", "stories", "explore", "accounts"}


def extract_username(profile_url: str) -> str:
    """Obtiene el username desde un enlace de perfil de Instagram.

    Acepta formatos como:
    - https://www.instagram.com/<username>/
    - https://instagram.com/<username>
    - http://instagram.com/<username>
    """
    parsed = urlparse(profile_url.strip())
    if not parsed.netloc:
        raise ValueError("URL inválida: faltan netloc/host")

    host = parsed.netloc.lower()
    if "instagram.com" not in host:
        raise ValueError("URL inválida: debe ser dominio instagram.com")

    # path: "/<username>/..."
    path = parsed.path.strip("/")
    if not path:
        raise ValueError("URL inválida: no contiene username en la ruta")

    first_segment = path.split("/")[0]
    if first_segment in _INVALID_FIRST_SEGMENTS:
        raise ValueError("URL no parece ser un perfil de usuario válido")

    if not _USERNAME_RE.match(first_segment):
        raise ValueError("Username contiene caracteres inválidos")

    return first_segment