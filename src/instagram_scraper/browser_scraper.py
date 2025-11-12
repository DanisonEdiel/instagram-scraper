from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from playwright.sync_api import sync_playwright

from .config import Config
from .utils import extract_username
from .auth import FacebookAuthenticator


logger = logging.getLogger(__name__)


class BrowserInstagramScraper:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.auth = FacebookAuthenticator(config)

    def get_profile_data(self, profile_url: str, posts_limit: Optional[int] = None) -> Dict[str, Any]:
        username = extract_username(profile_url)
        limit = posts_limit or self.config.posts_limit

        with sync_playwright() as pw:
            browser, context = self.auth.create_context_from_storage(pw)
            page = context.new_page()
            try:
                # Navega a la raíz para asegurar origen correcto
                page.goto("https://www.instagram.com/", timeout=30000)

                # Usa fetch desde el contexto para consultar la API web
                logger.info("Consultando API web_profile_info para %s", username)
                js = f"""
                async function fetchProfile(username) {{
                  const res = await fetch('https://www.instagram.com/api/v1/users/web_profile_info/?username=' + username, {{
                    headers: {{'x-ig-app-id': '936619743392459'}}
                  }});
                  if (!res.ok) throw new Error('HTTP ' + res.status);
                  return res.json();
                }}
                fetchProfile('{username}')
                """
                result = page.evaluate(js)

                user = result.get("data", {}).get("user")
                if not user:
                    raise RuntimeError("Respuesta inválida de la API de Instagram para el perfil solicitado")

                data: Dict[str, Any] = {
                    "username": user.get("username"),
                    "full_name": user.get("full_name"),
                    "biography": user.get("biography") or "",
                    "external_url": user.get("external_url"),
                    "is_verified": bool(user.get("is_verified")),
                    "is_private": bool(user.get("is_private")),
                    "profile_pic_url": user.get("profile_pic_url_hd") or user.get("profile_pic_url"),
                    "followers": user.get("edge_followed_by", {}).get("count"),
                    "following": user.get("edge_follow", {}).get("count"),
                    "posts_count": user.get("edge_owner_to_timeline_media", {}).get("count"),
                }

                edges = user.get("edge_owner_to_timeline_media", {}).get("edges", [])
                latest_posts: List[Dict[str, Any]] = []
                for edge in edges[:limit]:
                    node = edge.get("node", {})
                    shortcode = node.get("shortcode")
                    caption_edges = node.get("edge_media_to_caption", {}).get("edges", [])
                    caption = caption_edges[0].get("node", {}).get("text") if caption_edges else ""
                    taken_at = node.get("taken_at_timestamp")
                    latest_posts.append(
                        {
                            "shortcode": shortcode,
                            "url": f"https://www.instagram.com/p/{shortcode}/",
                            "date": None if not taken_at else __import__("datetime").datetime.utcfromtimestamp(taken_at).isoformat(),
                            "caption": caption,
                        }
                    )

                data["latest_posts"] = latest_posts
                return data

            finally:
                context.close()
                browser.close()