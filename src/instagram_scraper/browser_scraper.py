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
            try:
                browser, context = self.auth.create_context_from_storage(pw)
            except FileNotFoundError:
                browser = pw.chromium.launch(headless=self.config.headless)
                context = browser.new_context()
            page = context.new_page()
            try:
                # Navega a la raíz para asegurar origen correcto
                page.goto("https://www.instagram.com/", timeout=30000)

                # Usa fetch desde el contexto para consultar la API web
                logger.info("Consultando API web_profile_info para %s", username)
                js = (
                    "(async (u) => {\n"
                    "  const url = 'https://www.instagram.com/api/v1/users/web_profile_info/?username=' + encodeURIComponent(u);\n"
                    "  const res = await fetch(url, { headers: { 'x-ig-app-id': '936619743392459' } });\n"
                    "  if (!res.ok) throw new Error('HTTP ' + res.status);\n"
                    "  return res.json();\n"
                    "})('" + username + "')"
                )
                result = page.evaluate(js)

                user = result.get("data", {}).get("user")
                if not user:
                    raise RuntimeError("Respuesta inválida de la API de Instagram para el perfil solicitado")

                data: Dict[str, Any] = {
                    "username": user.get("username"),
                    "full_name": user.get("full_name"),
                    "biography": user.get("biography") or "",
                    "external_url": user.get("external_url"),
                    "is_private": bool(user.get("is_private")),
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

    def get_followers_counts_for_followers(
        self,
        profile_url: str,
        followers_limit: Optional[int] = None,
        page_size: int = 12,
        chunk: int = 2,
        delay_ms: int = 3000,
        retry_tries: int = 10,
        retry_base_ms: int = 2500,
    ) -> Dict[str, Any]:
        username = extract_username(profile_url)
        limit = followers_limit or 20
        with sync_playwright() as pw:
            try:
                browser, context = self.auth.create_context_from_storage(pw)
            except FileNotFoundError:
                browser = pw.chromium.launch(headless=self.config.headless)
                context = browser.new_context()
            page = context.new_page()
            try:
                page.goto("https://www.instagram.com/", timeout=30000)
                logger.info("Consultando seguidores y conteos para %s", username)
                try:
                    cookies = context.cookies()
                    has_session = any(c.get("name") == "sessionid" and c.get("value") for c in cookies)
                    logger.info("Autenticado: %s", "sí" if has_session else "no")
                    if not has_session:
                        raise RuntimeError("No hay sesión autenticada (cookie sessionid ausente). Ejecute 'auth' primero.")
                except Exception as e:
                    logger.error("Estado de sesión desconocido: %s", e)
                    raise
                js = (
                    "(async (u, total, pageSize, chunkSize, baseDelay, tries, baseRetryDelay) => {\n"
                    "  function sleep(ms){ return new Promise(r=>setTimeout(r, ms)); }\n"
                    "  async function fetchRetry(url, opts={}, triesParam=tries, delay=baseRetryDelay){\n"
                    "    for (let i=0; i<triesParam; i++){\n"
                    "      const res = await fetch(url, opts).catch(()=>null);\n"
                    "      if (res && res.ok) return res;\n"
                    "      const status = res ? res.status : 0;\n"
                    "      if (status===429 || status===0){\n"
                    "        const jitter = Math.floor(Math.random()*900);\n"
                    "        await sleep(delay + jitter);\n"
                    "        delay = Math.min(Math.floor(delay*1.7), 15000);\n"
                    "        continue;\n"
                    "      }\n"
                    "      throw new Error('HTTP ' + status);\n"
                    "    }\n"
                    "    throw new Error('Too many retries');\n"
                    "  }\n"
                    "  const h = { 'x-ig-app-id': '936619743392459', 'x-requested-with': 'XMLHttpRequest', 'referer': location.origin + '/' };\n"
                    "  const m = document.cookie.match(/csrftoken=([^;]+)/);\n"
                    "  if (m) h['x-csrftoken'] = m[1];\n"
                    "  const r1 = await fetchRetry('https://www.instagram.com/api/v1/users/web_profile_info/?username=' + encodeURIComponent(u), { headers: h });\n"
                    "  const j1 = await r1.json();\n"
                    "  const id = j1?.data?.user?.id;\n"
                    "  if (!id) throw new Error('no id');\n"
                    "  const totalFollowers = j1?.data?.user?.edge_followed_by?.count ?? null;\n"
                    "  let max_id = undefined;\n"
                    "  let users = [];\n"
                    "  for (;;) {\n"
                    "    const url = new URL('https://www.instagram.com/api/v1/friendships/' + id + '/followers/');\n"
                    "    url.searchParams.set('count', String(pageSize));\n"
                    "    if (max_id) url.searchParams.set('max_id', max_id);\n"
                    "    const r2 = await fetchRetry(url.toString(), { headers: h });\n"
                    "    const j2 = await r2.json();\n"
                    "    users = users.concat(j2?.users || []);\n"
                    "    max_id = j2?.next_max_id;\n"
                    "    if (!max_id || users.length >= total) break;\n"
                    "    await sleep(baseDelay);\n"
                    "  }\n"
                    "  users = users.slice(0, total);\n"
                    "  const out = [];\n"
                    "  for (let i = 0; i < users.length; i += chunkSize) {\n"
                    "    const part = users.slice(i, i + chunkSize);\n"
                    "    const results = await Promise.all(part.map(async it => {\n"
                    "      try {\n"
                    "        const r3 = await fetchRetry('https://www.instagram.com/api/v1/users/web_profile_info/?username=' + encodeURIComponent(it.username), { headers: h });\n"
                    "        const j3 = await r3.json();\n"
                    "        const c = j3?.data?.user?.edge_followed_by?.count ?? null;\n"
                    "        return { username: it.username, followers: c };\n"
                    "      } catch (e) {\n"
                    "        return { username: it.username, followers: null };\n"
                    "      }\n"
                    "    }));\n"
                    "    out.push(...results);\n"
                    "    await sleep(baseDelay);\n"
                    "  }\n"
                    "  return { username: j1?.data?.user?.username, count: totalFollowers, scraped_count: out.length, followers_of_followers: out };\n"
                    "})('" + username + "', " + str(limit) + ", " + str(page_size) + ", " + str(chunk) + ", " + str(delay_ms) + ", " + str(retry_tries) + ", " + str(retry_base_ms) + ")"
                )
                try:
                    result = page.evaluate(js)
                    try:
                        items = result.get("followers_of_followers", [])
                        logger.info("Items recogidos (API): %d", len(items))
                        for it in items[:50]:
                            logger.info("%s: %s", it.get("username"), str(it.get("followers")))
                        logger.info("Count (followers del perfil): %s", str(result.get("count")))
                    except Exception:
                        pass
                    return result
                except Exception:
                    # Fallback por UI: abrir modal de seguidores y scroll para recolectar usernames
                    page.goto(f"https://www.instagram.com/{username}/", timeout=30000)
                    try:
                        jscode_count = (
                            "(async (u, tries, baseDelay) => {\n"
                            "  function sleep(ms){ return new Promise(r=>setTimeout(r, ms)); }\n"
                            "  async function fetchRetry(url, opts={}, triesParam=tries, delay=baseDelay){\n"
                            "    for (let i=0; i<triesParam; i++){\n"
                            "      const res = await fetch(url, opts).catch(()=>null);\n"
                            "      if (res && res.ok) return res;\n"
                            "      const status = res ? res.status : 0;\n"
                            "      if (status===429 || status===0){ const jitter=Math.floor(Math.random()*900); await sleep(delay+jitter); delay=Math.min(Math.floor(delay*1.7),15000); continue;}\n"
                            "      throw new Error('HTTP ' + status);\n"
                            "    }\n"
                            "    throw new Error('Too many retries');\n"
                            "  }\n"
                            "  const h = { 'x-ig-app-id': '936619743392459', 'x-requested-with': 'XMLHttpRequest', 'referer': location.origin + '/' };\n"
                            "  const m = document.cookie.match(/csrftoken=([^;]+)/);\n"
                            "  if (m) h['x-csrftoken'] = m[1];\n"
                            "  const r = await fetchRetry('https://www.instagram.com/api/v1/users/web_profile_info/?username=' + encodeURIComponent(u), { headers: h });\n"
                            "  const j = await r.json();\n"
                            "  return j?.data?.user?.edge_followed_by?.count ?? null;\n"
                            ")('" + username + "', " + str(retry_tries) + ", " + str(retry_base_ms) + ")"
                        )
                        count_val = page.evaluate(jscode_count)
                    except Exception:
                        count_val = None
                    # Fallback: intenta leer el conteo directamente del DOM del perfil
                    if count_val is None:
                        try:
                            dom_count = page.evaluate(
                                """
(() => {
  function parseNum(txt){
    if (!txt) return null;
    const t = String(txt).trim();
    const m = t.match(/([0-9.,]+)\s*([kKmM])?/);
    if (!m) return null;
    let n = m[1].replace(/\s/g,'');
    n = n.replace(/\.(?=\d{3}\b)/g,'');
    n = n.replace(/,(?=\d{3}\b)/g,'');
    let val = Number(n.replace(',', '.'));
    const suf = m[2] ? m[2].toLowerCase() : '';
    if (suf==='k') val = Math.round(val*1000);
    if (suf==='m') val = Math.round(val*1000000);
    return Number.isFinite(val) ? val : null;
  }
  const selStr = `a[href$='/followers/'] span, a[href$='/followers/'] div, header section ul li a[href$='/followers/']`;
  const selectors = selStr.split(/\s*,\s*/);
  for (const sel of selectors){
    const el = document.querySelector(sel);
    if (el){
      const v = parseNum(el.textContent||el.innerText||'');
      if (v!==null) return v;
    }
  }
  const m = document.querySelector('meta[property="og:description"]');
  const t = m ? (m.getAttribute('content')||'') : '';
  const re = /([0-9.,]+)\s*(followers|seguidores)/i;
  const mm = t.match(re);
  if (mm){
    const v = parseNum(mm[1]);
    if (v!==null) return v;
  }
  return null;
})()
"""
                            )
                            count_val = dom_count
                        except Exception:
                            pass
                    # Detecta si el perfil es privado y devuelve temprano con mensaje claro
                    try:
                        is_private = page.evaluate(
                            "(() => {\n"
                            "  const text = (document.body && document.body.innerText) ? document.body.innerText : '';\n"
                            "  return /(this account is private|esta cuenta es privada|cuenta privada)/i.test(text);\n"
                            "})()"
                        )
                        if is_private:
                            logger.warning("Perfil privado: el listado de seguidores no está disponible si no sigues la cuenta")
                            return {"username": username, "count": count_val, "followers_of_followers": []}
                    except Exception:
                        pass
                    try:
                        for btn in [
                            page.get_by_role("button", name="Permitir todas las cookies").first,
                            page.get_by_role("button", name="Allow all cookies").first,
                            page.get_by_role("button", name="Aceptar").first,
                        ]:
                            if btn.is_visible():
                                btn.click()
                                break
                    except Exception:
                        pass
                    page.wait_for_selector("a[href$='/followers/']", timeout=20000)
                    page.locator("a[href$='/followers/']").first.click()
                    page.wait_for_selector("div[role='dialog']", timeout=20000)
                    usernames: List[str] = []
                    last_len = -1
                    unchanged_rounds = 0
                    # Scrollea y extrae varias veces para cargar elementos virtualizados del diálogo
                    while len(usernames) < limit:
                        # Primero intenta desplazar para forzar carga
                        try:
                            page.evaluate(
                                "(() => {\n"
                                "  const dlg = document.querySelector('div[role=\"dialog\"]');\n"
                                "  if (!dlg) return false;\n"
                                "  const nodes = [dlg, ...Array.from(dlg.querySelectorAll('*'))];\n"
                                "  const sc = nodes.find(n => (n.scrollHeight||0) > (n.clientHeight||0));\n"
                                "  if (!sc) return false;\n"
                                "  sc.scrollTop = sc.scrollHeight;\n"
                                "  return true;\n"
                                "})()"
                            )
                        except Exception:
                            page.mouse.wheel(0, 3000)
                        page.wait_for_timeout(800)

                        # Luego extrae usernames visibles
                        try:
                            found = page.evaluate(
                                "(() => {\n"
                                "  const dlg = document.querySelector('div[role=\"dialog\"]');\n"
                                "  if (!dlg) return [];\n"
                                "  const anchors = Array.from(dlg.querySelectorAll('a[href^=\"/\"][href$=\"/\"], a[role=\"link\"][href^=\"/\"][href$=\"/\"]'));\n"
                                "  const out = [];\n"
                                "  for (const a of anchors) {\n"
                                "    const href = a.getAttribute('href') || '';\n"
                                "    const m = href.match(/^\\/([A-Za-z0-9._]+)\\/$/);\n"
                                "    if (m) out.push(m[1]);\n"
                                "  }\n"
                                "  return Array.from(new Set(out));\n"
                                "})()"
                            )
                        except Exception:
                            found = []
                        try:
                            logger.info("Usernames visibles en diálogo: %d", len(found))
                        except Exception:
                            pass
                        for uname in found:
                            if uname not in usernames:
                                usernames.append(uname)
                                if len(usernames) >= limit:
                                    break
                        if len(usernames) == last_len:
                            unchanged_rounds += 1
                        else:
                            unchanged_rounds = 0
                        last_len = len(usernames)
                        if unchanged_rounds >= 5:
                            try:
                                logger.info(
                                    "Sin nuevos usernames tras %d rondas; procesando %d usuarios",
                                    unchanged_rounds,
                                    len(usernames),
                                )
                            except Exception:
                                pass
                            break
                        page.wait_for_timeout(max(delay_ms, 1200))

                    out: List[Dict[str, Any]] = []
                    for uname in usernames[:limit]:
                        jscode = (
                            "(async (u, tries, baseDelay) => {\n"
                            "  function sleep(ms){ return new Promise(r=>setTimeout(r, ms)); }\n"
                            "  async function fetchRetry(url, opts={}, triesParam=tries, delay=baseDelay){\n"
                            "    for (let i=0; i<triesParam; i++){\n"
                            "      const res = await fetch(url, opts).catch(()=>null);\n"
                            "      if (res && res.ok) return res;\n"
                            "      const status = res ? res.status : 0;\n"
                            "      if (status===429 || status===0){ const jitter=Math.floor(Math.random()*900); await sleep(delay+jitter); delay=Math.min(Math.floor(delay*1.7),15000); continue;}\n"
                            "      throw new Error('HTTP ' + status);\n"
                            "    }\n"
                            "    throw new Error('Too many retries');\n"
                            "  }\n"
                            "  const h = { 'x-ig-app-id': '936619743392459', 'x-requested-with': 'XMLHttpRequest', 'referer': location.origin + '/' };\n"
                            "  const m = document.cookie.match(/csrftoken=([^;]+)/);\n"
                            "  if (m) h['x-csrftoken'] = m[1];\n"
                            "  const r = await fetchRetry('https://www.instagram.com/api/v1/users/web_profile_info/?username=' + encodeURIComponent(u), { headers: h });\n"
                            "  const j = await r.json();\n"
                            "  const c = j?.data?.user?.edge_followed_by?.count ?? null;\n"
                            "  return { username: u, followers: c };\n"
                            ")('" + uname + "', " + str(retry_tries) + ", " + str(retry_base_ms) + ")"
                        )
                        try:
                            item = page.evaluate(jscode)
                            out.append(item)
                            page.wait_for_timeout(1000)
                        except Exception:
                            out.append({"username": uname, "followers": None})
                            page.wait_for_timeout(1500)
                        if out[-1].get("followers") is None:
                            try:
                                page.goto(f"https://www.instagram.com/{uname}/", timeout=30000)
                                dom_val = page.evaluate(
                                    "(() => {\n"
                                    "  function parseNum(txt){\n"
                                    "    if (!txt) return null;\n"
                                    "    const t = String(txt).trim();\n"
                                    "    const m = t.match(/([0-9.,]+)\\s*([kKmM])?/);\n"
                                    "    if (!m) return null;\n"
                                    "    let n = m[1].replace(/\\s/g,'');\n"
                                    "    n = n.replace(/\\.(?=\\d{3}\\b)/g,'');\n"
                                    "    n = n.replace(/,(?=\\d{3}\\b)/g,'');\n"
                                    "    let val = Number(n.replace(',', '.'));\n"
                                    "    const suf = m[2] ? m[2].toLowerCase() : '';\n"
                                    "    if (suf==='k') val = Math.round(val*1000);\n"
                                    "    if (suf==='m') val = Math.round(val*1000000);\n"
                                    "    return Number.isFinite(val) ? val : null;\n"
                                    "  }\n"
                                    "  const candidates = Array.from(document.querySelectorAll(`a[href$='/followers/'] span, a[href$='/followers/'] div, header section ul li a[href$='/followers/']`));\n"
                                    "  for (const el of candidates){\n"
                                    "    const v = parseNum(el.textContent||el.innerText||'');\n"
                                    "    if (v!==null) return v;\n"
                                    "  }\n"
                                    "  const m = document.querySelector('meta[property=\"og:description\"]');\n"
                                    "  const t = m ? (m.getAttribute('content')||'') : '';\n"
                                    "  const re = /([0-9.,]+)\\s*(followers|seguidores)/i;\n"
                                    "  const mm = t.match(re);\n"
                                    "  if (mm){\n"
                                    "    const v = parseNum(mm[1]);\n"
                                    "    if (v!==null) return v;\n"
                                    "  }\n"
                                    "  return null;\n"
                                    "})()"
                                )
                                out[-1] = {"username": uname, "followers": dom_val}
                            except Exception:
                                pass
                    try:
                        logger.info("Items recogidos (UI): %d", len(out))
                        for it in out[:50]:
                            logger.info("%s: %s", it.get("username"), str(it.get("followers")))
                        logger.info("Count (followers del perfil): %s", str(count_val))
                    except Exception:
                        pass
                    return {"username": username, "count": count_val, "followers_of_followers": out}
            finally:
                context.close()
                browser.close()