from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from playwright.sync_api import Playwright, sync_playwright, TimeoutError as PlaywrightTimeout

from .config import Config


logger = logging.getLogger(__name__)


def _get_fernet(secret_key: Optional[str]) -> Optional[Fernet]:
    if not secret_key:
        return None
    try:
        return Fernet(secret_key)
    except Exception:
        raise ValueError("AUTH_SECRET_KEY inválida. Debe ser una clave Fernet base64")


class FacebookAuthenticator:
    def __init__(self, config: Config) -> None:
        self.config = config
        self._fernet = _get_fernet(config.auth_secret_key)

    def _encrypt_file(self, src_path: Path, dest_path: Path) -> None:
        if not self._fernet:
            # Si no hay cifrado, copia como texto
            dest_path.write_bytes(src_path.read_bytes())
            return
        data = src_path.read_bytes()
        encrypted = self._fernet.encrypt(data)
        dest_path.write_bytes(encrypted)

    def _decrypt_to_text(self, enc_path: Path) -> str:
        raw = enc_path.read_bytes()
        if not self._fernet:
            return raw.decode("utf-8")
        try:
            dec = self._fernet.decrypt(raw)
            return dec.decode("utf-8")
        except InvalidToken:
            raise RuntimeError("No se pudo descifrar el storage de autenticación. Clave incorrecta.")

    def login_with_facebook(self) -> None:
        """
        Automatiza el flujo de "Iniciar sesión con Facebook" y guarda el estado de sesión.
        - Guarda storage_state.json en `storage_plain_path`
        - Cifra y guarda en `storage_path` si hay AUTH_SECRET_KEY
        """
        if not (self.config.fb_email and self.config.fb_password):
            raise RuntimeError("Debe definir FB_EMAIL y FB_PASSWORD en variables de entorno para login con Facebook")

        plain_path = Path(self.config.storage_plain_path)
        enc_path = Path(self.config.storage_path)
        plain_path.parent.mkdir(parents=True, exist_ok=True)
        enc_path.parent.mkdir(parents=True, exist_ok=True)

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=self.config.headless, args=["--disable-blink-features=AutomationControlled"])
            context = browser.new_context()
            page = context.new_page()

            try:
                logger.info("Navegando a la página de login de Instagram")
                page.goto("https://www.instagram.com/accounts/login/", timeout=30000)
                page.wait_for_load_state("domcontentloaded")

                # Manejar pantalla de bienvenida con "Continuar" / "Usar otro perfil" si aparece
                logger.info("Comprobando si aparece pantalla 'Continuar' o 'Usar otro perfil'")
                try:
                    cont_buttons = [
                        page.get_by_role("button", name="Continuar").first,
                        page.get_by_role("button", name="Continue").first,
                    ]
                    for btn in cont_buttons:
                        if btn.is_visible():
                            logger.info("Se encontró botón 'Continuar'; haciendo clic")
                            btn.click()
                            break
                except Exception:
                    pass

                try:
                    other_profile_buttons = [
                        page.get_by_role("button", name="Usar otro perfil").first,
                        page.get_by_role("button", name="Use another profile").first,
                    ]
                    for btn in other_profile_buttons:
                        if btn.is_visible():
                            logger.info("Se encontró botón 'Usar otro perfil'; haciendo clic")
                            btn.click()
                            # Volver a la página de login explícita por si redirige a home
                            page.goto("https://www.instagram.com/accounts/login/", timeout=30000)
                            page.wait_for_load_state("domcontentloaded")
                            break
                except Exception:
                    pass

                # Aceptar posibles banners de cookies que bloqueen interacción
                try:
                    cookie_buttons = [
                        page.get_by_role("button", name="Permitir todas las cookies").first,
                        page.get_by_role("button", name="Allow all cookies").first,
                        page.get_by_role("button", name="Aceptar").first,
                    ]
                    for btn in cookie_buttons:
                        if btn.is_visible():
                            logger.info("Aceptando cookies para continuar")
                            btn.click()
                            break
                except Exception:
                    pass

                # Si ya hay sesión activa después de 'Continuar', guardar y salir
                cookies_after = context.cookies()
                has_session_early = any(c.get("name") == "sessionid" and c.get("value") for c in cookies_after)
                if has_session_early:
                    logger.info("Sesión detectada tras 'Continuar'; guardando estado sin login con Facebook")
                    state_json = context.storage_state()
                    Path(self.config.storage_plain_path).write_text(json.dumps(state_json), encoding="utf-8")
                    self._encrypt_file(Path(self.config.storage_plain_path), Path(self.config.storage_path))
                    logger.info("Estado de sesión guardado y cifrado correctamente")
                    return

                logger.info("Haciendo clic en 'Iniciar sesión con Facebook'")
                fb_button_locators = [
                    page.get_by_role("button", name="Iniciar sesión con Facebook").first,
                    page.get_by_role("button", name="Log in with Facebook").first,
                    page.get_by_role("button", name="Continuar con Facebook").first,
                    page.get_by_role("button", name="Continue with Facebook").first,
                    page.locator("button:has-text('Log in with Facebook')").first,
                    page.locator("button:has-text('Iniciar sesión con Facebook')").first,
                    page.locator("button:has-text('Continuar con Facebook')").first,
                    page.locator("button:has-text('Continue with Facebook')").first,
                    page.locator("a[href*='facebook.com']").first,
                ]

                clicked = False
                fb_page = None
                # Intentar capturar ventana emergente si el clic abre un popup
                for loc in fb_button_locators:
                    try:
                        try:
                            loc.scroll_into_view_if_needed()
                        except Exception:
                            pass
                        loc.click(timeout=7000)
                        try:
                            page.wait_for_url("**facebook.com**", timeout=7000)
                            fb_page = page
                            clicked = True
                            break
                        except PlaywrightTimeout:
                            try:
                                pages = page.context.pages
                                for p in pages:
                                    if "facebook.com" in (p.url or ""):
                                        fb_page = p
                                        clicked = True
                                        break
                                if clicked:
                                    break
                            except Exception:
                                pass
                    except PlaywrightTimeout:
                        try:
                            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                            try:
                                loc.scroll_into_view_if_needed()
                            except Exception:
                                pass
                            loc.click(timeout=7000)
                            try:
                                page.wait_for_url("**facebook.com**", timeout=7000)
                                fb_page = page
                                clicked = True
                                break
                            except PlaywrightTimeout:
                                try:
                                    pages = page.context.pages
                                    for p in pages:
                                        if "facebook.com" in (p.url or ""):
                                            fb_page = p
                                            clicked = True
                                            break
                                    if clicked:
                                        break
                                except Exception:
                                    pass
                        except PlaywrightTimeout:
                            continue

                if not clicked:
                    try:
                        page.evaluate("var e=document.querySelector(\"a[href*='facebook.com']\"); if(e){e.click()}else{throw new Error('no-link')}")
                        fb_page = page
                        clicked = True
                    except Exception:
                        raise RuntimeError("No se encontró o no se pudo hacer clic en el botón de login con Facebook en Instagram")

                logger.info("Esperando redirección a Facebook")
                # Si hubo popup, esperar la URL en el popup; si no, en la misma página
                (fb_page or page).wait_for_url("**facebook.com**", timeout=30000)

                logger.info("Introduciendo credenciales de Facebook")
                fb_page.wait_for_load_state("domcontentloaded")

                # Aceptar posible banner de cookies en Facebook que bloquee interacción
                try:
                    fb_cookie_buttons = [
                        fb_page.get_by_role("button", name="Permitir todas las cookies").first,
                        fb_page.get_by_role("button", name="Allow all cookies").first,
                        fb_page.get_by_role("button", name="Aceptar todo").first,
                        fb_page.get_by_role("button", name="Solo esenciales").first,
                        fb_page.get_by_role("button", name="Only essential").first,
                    ]
                    for btn in fb_cookie_buttons:
                        if btn.is_visible():
                            logger.info("Aceptando cookies en Facebook")
                            btn.click()
                            break
                except Exception:
                    pass

                # Localizadores alternativos para inputs, con espera de visibilidad
                email_candidates = [
                    fb_page.locator("input[name='email']"),
                    fb_page.get_by_placeholder("Correo electrónico o número de teléfono").first,
                    fb_page.get_by_placeholder("Email or phone number").first,
                    fb_page.get_by_placeholder("Número de móvil o correo electrónico").first,
                    fb_page.get_by_placeholder("Phone number or email").first,
                ]
                pass_candidates = [
                    fb_page.locator("input[name='pass']"),
                    fb_page.get_by_placeholder("Contraseña").first,
                    fb_page.get_by_placeholder("Password").first,
                ]

                email_filled = False
                for loc in email_candidates:
                    try:
                        loc.wait_for(state="visible", timeout=8000)
                        fb_page.evaluate("window.scrollTo(0, 0)")
                        loc.click()
                        loc.fill(self.config.fb_email)
                        email_filled = True
                        break
                    except PlaywrightTimeout:
                        continue
                    except Exception:
                        continue

                if not email_filled:
                    # Fallback por si un overlay bloquea la interacción con el input
                    try:
                        fb_page.evaluate("document.querySelector('input[name=\"email\"]').value = arguments[0]", self.config.fb_email)
                        email_filled = True
                    except Exception:
                        try:
                            for fr in fb_page.frames:
                                try:
                                    el = fr.query_selector("input[name='email']")
                                    if el:
                                        el.fill(self.config.fb_email)
                                        email_filled = True
                                        break
                                except Exception:
                                    continue
                        except Exception:
                            pass

                pwd_filled = False
                for loc in pass_candidates:
                    try:
                        loc.wait_for(state="visible", timeout=8000)
                        loc.click()
                        loc.fill(self.config.fb_password)
                        pwd_filled = True
                        break
                    except PlaywrightTimeout:
                        continue
                    except Exception:
                        continue

                if not pwd_filled:
                    try:
                        fb_page.evaluate("document.querySelector('input[name=\"pass\"]').value = arguments[0]", self.config.fb_password)
                        pwd_filled = True
                    except Exception:
                        try:
                            for fr in fb_page.frames:
                                try:
                                    el = fr.query_selector("input[name='pass']")
                                    if el:
                                        el.fill(self.config.fb_password)
                                        pwd_filled = True
                                        break
                                except Exception:
                                    continue
                        except Exception:
                            pass

                if not (email_filled and pwd_filled):
                    if self.config.headless:
                        raise RuntimeError("No se pudo completar los campos de email y contraseña en Facebook")
                    else:
                        logger.info("No se pudo rellenar automáticamente; introduzca email y contraseña manualmente y pulse 'Iniciar sesión'")

                # Click en botón de iniciar sesión con variantes
                login_buttons = [
                    fb_page.get_by_role("button", name="Iniciar sesión").first,
                    fb_page.get_by_role("button", name="Log In").first,
                    fb_page.get_by_role("button", name="Acceder").first,
                    fb_page.get_by_role("button", name="Sign in").first,
                    fb_page.locator("button[name='login']").first,
                    fb_page.locator("button:has-text('Iniciar sesión')").first,
                    fb_page.locator("button:has-text('Log In')").first,
                ]
                clicked_login = False
                for btn in login_buttons:
                    try:
                        if btn.is_visible():
                            btn.click()
                            clicked_login = True
                            break
                    except Exception:
                        continue

                if not clicked_login:
                    # Fallback JS por si el botón está detrás de overlays
                    try:
                        fb_page.evaluate("document.querySelector('button[name=\"login\"]').click()")
                        clicked_login = True
                    except Exception:
                        try:
                            for fr in fb_page.frames:
                                try:
                                    el = fr.query_selector("button[name='login']")
                                    if el:
                                        el.click()
                                        clicked_login = True
                                        break
                                except Exception:
                                    continue
                        except Exception:
                            pass

                if not clicked_login:
                    if self.config.headless:
                        raise RuntimeError("No se pudo hacer clic en el botón 'Iniciar sesión' de Facebook")
                    else:
                        logger.info("No se pudo hacer clic automáticamente; pulse 'Iniciar sesión' manualmente")

                # Posibles pantallas adicionales: confirmación "Continue as"
                try:
                    fb_page.wait_for_selector("text=Continuar como", timeout=8000)
                    fb_page.get_by_role("button", name="Continuar como").first.click()
                except PlaywrightTimeout:
                    pass

                # Manejo básico de 2FA si está presente
                try:
                    twofa_input = fb_page.locator("input[name='approvals_code']")
                    if twofa_input.is_visible():
                        if self.config.fb_2fa_code:
                            twofa_input.fill(self.config.fb_2fa_code)
                            fb_page.click("button[name='submit[Submit Code]']")
                        else:
                            if self.config.headless:
                                raise RuntimeError("Se requiere FB_2FA_CODE para completar 2FA de Facebook")
                            else:
                                try:
                                    fb_page.wait_for_selector("input[name='approvals_code']", state="hidden", timeout=120000)
                                except PlaywrightTimeout:
                                    pass
                except PlaywrightTimeout:
                    pass

                try:
                    try:
                        fb_page.wait_for_selector("iframe[src*='recaptcha'], text=No soy un robot", timeout=3000)
                        logger.info("Detectado reCAPTCHA en Facebook")
                        if self.config.headless:
                            raise RuntimeError("reCAPTCHA detectado; ejecute con HEADLESS=false para resolver manualmente")
                        try:
                            try:
                                fb_page.frame_locator("iframe[src*='recaptcha']").locator(".recaptcha-checkbox-border").click()
                            except Exception:
                                pass
                            fb_page.wait_for_selector("iframe[src*='recaptcha']", state="hidden", timeout=120000)
                        except PlaywrightTimeout:
                            pass
                    except PlaywrightTimeout:
                        pass
                except Exception:
                    pass

                logger.info("Esperando retorno a Instagram autenticado")
                try:
                    page.wait_for_url("**instagram.com**", timeout=120000)
                except PlaywrightTimeout:
                    (fb_page or page).wait_for_url("**instagram.com**", timeout=120000)

                # Verificar sesión por cookie 'sessionid'
                cookies = context.cookies()
                has_session = any(c.get("name") == "sessionid" and c.get("value") for c in cookies)
                if not has_session:
                    for _ in range(12):
                        time.sleep(5)
                        cookies = context.cookies()
                        has_session = any(c.get("name") == "sessionid" and c.get("value") for c in cookies)
                        if has_session:
                            break
                if not has_session:
                    raise RuntimeError("Login no parece haber sido exitoso: cookie 'sessionid' no encontrada")

                # Guardar estado
                state_json = context.storage_state()
                Path(self.config.storage_plain_path).write_text(json.dumps(state_json), encoding="utf-8")
                self._encrypt_file(Path(self.config.storage_plain_path), Path(self.config.storage_path))
                logger.info("Estado de sesión guardado y cifrado correctamente")

            finally:
                context.close()
                browser.close()

    def create_context_from_storage(self, pw: Playwright):
        """Crea un contexto Playwright usando el storage_state descifrado."""
        enc_path = Path(self.config.storage_path)
        if not enc_path.exists():
            raise FileNotFoundError("No se encontró el archivo de storage cifrado. Ejecute el comando de autenticación primero.")
        decrypted = self._decrypt_to_text(enc_path)
        storage_state = json.loads(decrypted)
        browser = pw.chromium.launch(headless=self.config.headless)
        context = browser.new_context(storage_state=storage_state)
        return browser, context