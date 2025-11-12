from __future__ import annotations

from typing import Any, Dict, List, Optional
import logging

from instaloader import (
    Instaloader,
    Profile,
    TwoFactorAuthRequiredException,
    BadCredentialsException,
)

from .config import Config
from .utils import extract_username


class InstagramScraper:
    def __init__(self, config: Config) -> None:
        # Configuramos Instaloader evitando descargas de archivos
        self.loader = Instaloader(
            download_pictures=False,
            download_videos=False,
            download_video_thumbnails=False,
            download_geotags=False,
            save_metadata=False,
            post_metadata_txt_pattern="",
        )
        self.config = config
        self.logger = logging.getLogger(__name__)

    def login_if_available(self) -> None:
        """Realiza login si hay credenciales en la configuración."""
        if not (self.config.ig_username and self.config.ig_password):
            return
        try:
            self.loader.login(self.config.ig_username, self.config.ig_password)
        except TwoFactorAuthRequiredException:
            if not self.config.ig_2fa_code:
                raise RuntimeError(
                    "Se requiere IG_2FA_CODE para completar el login con 2FA"
                )
            self.loader.two_factor_login(self.config.ig_2fa_code)
        except BadCredentialsException as e:
            raise RuntimeError("Credenciales de Instagram inválidas") from e

    def get_profile_data(self, profile_url: str, posts_limit: Optional[int] = None) -> Dict[str, Any]:
        """Obtiene datos de un perfil y una lista acotada de posts recientes."""
        username = extract_username(profile_url)
        ctx = self.loader.context
        self.logger.info("Cargando perfil de %s via Instaloader", username)
        profile = Profile.from_username(ctx, username)

        data: Dict[str, Any] = {
            "username": profile.username,
            "full_name": profile.full_name,
            "biography": profile.biography or "",
            "external_url": profile.external_url,
            "is_verified": profile.is_verified,
            "is_private": profile.is_private,
            "profile_pic_url": str(profile.profile_pic_url),
            "followers": profile.followers,
            "following": profile.followees,
            "posts_count": profile.mediacount,
        }

        limit = posts_limit or self.config.posts_limit
        latest_posts: List[Dict[str, Any]] = []

        # Para perfiles privados sin permisos, no habrá posts
        if not profile.is_private or (self.config.ig_username and self.config.ig_password):
            for post in profile.get_posts():
                latest_posts.append(
                    {
                        "shortcode": post.shortcode,
                        "url": f"https://www.instagram.com/p/{post.shortcode}/",
                        "date": post.date_utc.isoformat(),
                        "caption": post.caption or "",
                    }
                )
                if len(latest_posts) >= limit:
                    break

        data["latest_posts"] = latest_posts
        return data