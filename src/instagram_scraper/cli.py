from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from .config import load_config
from .scraper import InstagramScraper
from .auth import FacebookAuthenticator
from .browser_scraper import BrowserInstagramScraper


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Instagram Scraper con login Facebook (OAuth)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Subcomando de autenticación
    auth_parser = subparsers.add_parser("auth", help="Autenticación con Facebook y guardado de sesión")
    auth_parser.add_argument("--headless", type=str, default=None, help="true/false para ejecutar en modo headless")

    # Subcomando de scraping con sesión Playwright
    scrape_parser = subparsers.add_parser("scrape", help="Scrapear perfil usando sesión autenticada")
    scrape_parser.add_argument("--url", required=True, help="Enlace del perfil de Instagram")
    scrape_parser.add_argument("--posts", type=int, default=None, help="Cantidad de posts recientes (por defecto POSTS_LIMIT)")
    scrape_parser.add_argument("--output", type=Path, default=None, help="Archivo de salida JSON (opcional)")

    # Subcomando de scraping con Instaloader (opcional)
    legacy_parser = subparsers.add_parser("legacy", help="Scrapear con Instaloader (login IG opcional)")
    legacy_parser.add_argument("--url", required=True, help="Enlace del perfil de Instagram")
    legacy_parser.add_argument("--posts", type=int, default=None, help="Cantidad de posts recientes (por defecto POSTS_LIMIT)")
    legacy_parser.add_argument("--output", type=Path, default=None, help="Archivo de salida JSON (opcional)")
    legacy_parser.add_argument("--login", action="store_true", help="Intentar login IG con usuario/contraseña")

    return parser


def main() -> None:
    config = load_config()
    logging.basicConfig(level=getattr(logging, config.log_level.upper(), logging.INFO), format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    parser = build_parser()
    args = parser.parse_args()

    if args.command == "auth":
        if args.headless is not None:
            config.headless = args.headless.lower() == "true"
        auth = FacebookAuthenticator(config)
        auth.login_with_facebook()
        print("Autenticación completada y sesión guardada.")
        return

    elif args.command == "scrape":
        scraper = BrowserInstagramScraper(config)
        data = scraper.get_profile_data(args.url, posts_limit=args.posts)
    elif args.command == "legacy":
        scraper = InstagramScraper(config)
        if getattr(args, "login", False):
            scraper.login_if_available()
        data = scraper.get_profile_data(args.url, posts_limit=args.posts)
    else:
        parser.error("Comando no reconocido")
        return

    output = json.dumps(data, ensure_ascii=False, indent=2)
    print(output)

    out_path = getattr(args, "output", None)
    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output, encoding="utf-8")