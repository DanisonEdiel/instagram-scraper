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

    def get_following_details(
        self,
        profile_url: str,
        following_limit: Optional[int] = None,
        page_size: int = 12,
        chunk: int = 2,
        delay_ms: int = 3000,
        retry_tries: int = 10,
        retry_base_ms: int = 2500,
        force_ui: bool = False,
    ) -> Dict[str, Any]:
        """Obtiene los usuarios que el perfil sigue (following) y detalles por cada uno.

        Devuelve: {
          username: <perfil consultado>,
          following_count: <número de seguidos del perfil>,
          scraped_count: <procesados>,
          following_details: [ { username, full_name, biography, account_type, category, followers, following, url } ]
        }
        """
        username = extract_username(profile_url)
        limit = following_limit or 20
        with sync_playwright() as pw:
            try:
                browser, context = self.auth.create_context_from_storage(pw)
            except FileNotFoundError:
                browser = pw.chromium.launch(headless=self.config.headless)
                context = browser.new_context()
            page = context.new_page()
            try:
                page.goto("https://www.instagram.com/", timeout=30000)
                logger.info("Consultando seguidos y detalles para %s", username)
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
                    "      if (status===429 || status===0){ const jitter=Math.floor(Math.random()*900); await sleep(delay+jitter); delay=Math.min(Math.floor(delay*1.7),15000); continue;}\n"
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
                    "  const followingCount = j1?.data?.user?.edge_follow?.count ?? null;\n"
                    "  let max_id = undefined;\n"
                    "  let users = [];\n"
                    "  for (;;) {\n"
                    "    const url = new URL('https://www.instagram.com/api/v1/friendships/' + id + '/following/');\n"
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
                    "        const udata = j3?.data?.user || {};\n"
                    "        const accType = (udata?.is_professional ? (udata?.is_business_account ? 'empresa' : 'creador') : 'personal');\n"
                    "        return {\n"
                    "          username: it.username,\n"
                    "          full_name: udata?.full_name ?? null,\n"
                    "          biography: udata?.biography ?? '',\n"
                    "          account_type: accType,\n"
                    "          category: udata?.category_name ?? null,\n"
                    "          followers: udata?.edge_followed_by?.count ?? null,\n"
                    "          following: udata?.edge_follow?.count ?? null,\n"
                    "          url: location.origin + '/' + it.username + '/'\n"
                    "        };\n"
                    "      } catch (e) {\n"
                    "        return { username: it.username, full_name: null, biography: '', account_type: null, category: null, followers: null, following: null, url: location.origin + '/' + it.username + '/' };\n"
                    "      }\n"
                    "    }));\n"
                    "    out.push(...results);\n"
                    "    await sleep(baseDelay);\n"
                    "  }\n"
                    "  return { username: j1?.data?.user?.username, following_count: followingCount, scraped_count: out.length, following_details: out };\n"
                    ")('" + username + "', " + str(limit) + ", " + str(page_size) + ", " + str(chunk) + ", " + str(delay_ms) + ", " + str(retry_tries) + ", " + str(retry_base_ms) + ")"
                )
                if not force_ui:
                    try:
                        result = page.evaluate(js)
                        items = result.get("following_details", []) or []
                        logger.info("Items recogidos (API): %d", len(items))
                        # Enriquecimiento: para cualquier item con campos vacíos, intenta HTML y luego DOM.
                        enriched: List[Dict[str, Any]] = []
                        for it in items:
                            uname = it.get("username") or ""
                            before = {
                                "full_name": it.get("full_name"),
                                "biography": it.get("biography"),
                                "followers": it.get("followers"),
                                "following": it.get("following"),
                            }
                            need_fb = (not it.get("full_name")) or (it.get("followers") is None) or (it.get("following") is None) or (not it.get("biography"))
                            used_html = False
                            used_dom = False
                            if need_fb and uname:
                                # 1) Intento rápido vía HTML (og tags)
                                fb_js = (
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
                                    "  const h = { 'x-requested-with': 'XMLHttpRequest', 'referer': location.origin + '/' };\n"
                                    "  const r = await fetchRetry(location.origin + '/' + u + '/', { headers: h });\n"
                                    "  const html = await r.text();\n"
                                    "  const doc = new DOMParser().parseFromString(html, 'text/html');\n"
                                    "  function parseNum(txt){\n"
                                    "    if (!txt) return null;\n"
                                    "    const t = String(txt).trim();\n"
                                    "    const m = t.match(/([0-9.,]+)\\s*(k|m|K|M|mil|millones|millon|millón)?/i);\n"
                                    "    if (!m) return null;\n"
                                    "    let n = m[1].replace(/\\s/g,'');\n"
                                    "    n = n.replace(/\\.(?=\\d{3}\\b)/g,'');\n"
                                    "    n = n.replace(/,(?=\\d{3}\\b)/g,'');\n"
                                    "    let val = Number(n.replace(',', '.'));\n"
                                    "    const suf = m[2] ? m[2].toLowerCase() : '';\n"
                                    "    if (suf==='k') val = Math.round(val*1000);\n"
                                    "    if (suf==='m') val = Math.round(val*1000000);\n"
                                    "    if (suf==='mil') val = Math.round(val*1000);\n"
                                    "    if (suf==='millones' || suf==='millon' || suf==='millón') val = Math.round(val*1000000);\n"
                                    "    return Number.isFinite(val) ? val : null;\n"
                                    "  }\n"
                                    "  let fullName = null;\n"
                                    "  const metaTitle = doc.querySelector('meta[property=\"og:title\"]');\n"
                                    "  if (metaTitle) { const t = metaTitle.getAttribute('content')||''; const mt = t.match(/^(.+?)\\s\\(@/); if (mt) fullName = mt[1].trim(); }\n"
                                    "  let followers = null, following = null;\n"
                                    "  const mdesc = doc.querySelector('meta[property=\"og:description\"]');\n"
                                    "  if (mdesc) { const t = mdesc.getAttribute('content')||''; const mf = t.match(/([0-9.,]+)\\s*(followers|seguidores)/i); const mg = t.match(/([0-9.,]+)\\s*(following|seguidos)/i); if (mf) followers = parseNum(mf[1]); if (mg) following = parseNum(mg[1]); }\n"
                                    "  // Intento simple de bio\n"
                                    "  let biography = '';\n"
                                    "  const bioMeta = doc.querySelector('[data-testid=\"user-bio\"]');\n"
                                    "  if (bioMeta) { const t = bioMeta.textContent||bioMeta.innerText||''; if (t && t.trim().length>=3) biography = t.trim(); }\n"
                                    "  return { full_name: fullName, biography, followers, following };\n"
                                    ")('" + uname + "', " + str(retry_tries) + ", " + str(retry_base_ms) + ")"
                                )
                                try:
                                    fb = page.evaluate(fb_js)
                                    used_html = True
                                    if not it.get("full_name") and fb.get("full_name"):
                                        it["full_name"] = fb.get("full_name")
                                    if (it.get("followers") is None) and (fb.get("followers") is not None):
                                        it["followers"] = fb.get("followers")
                                    if (it.get("following") is None) and (fb.get("following") is not None):
                                        it["following"] = fb.get("following")
                                    if not it.get("biography") and fb.get("biography"):
                                        it["biography"] = fb.get("biography")
                                except Exception:
                                    pass
                                # 2) Si sigue faltando algo crítico, intenta DOM navegando al perfil
                                if (not it.get("full_name")) or (it.get("followers") is None) or (it.get("following") is None) or (not it.get("biography")):
                                    try:
                                        page.goto(f"https://www.instagram.com/{uname}/", timeout=30000)
                                        page.wait_for_load_state("domcontentloaded")
                                        page.wait_for_timeout(500)
                                        dom_vals = page.evaluate(
                                            "(() => {\n"
                                            "  function parseNum(txt){ if (!txt) return null; const t=String(txt).trim(); const m=t.match(/([0-9.,]+)\\s*(k|m|K|M|mil|millones|millon|millón)?/i); if(!m) return null; let n=m[1].replace(/\\s/g,''); n=n.replace(/\\.(?=\\d{3}\\b)/g,''); n=n.replace(/,(?=\\d{3}\\b)/g,''); let val=Number(n.replace(',', '.')); const suf=m[2]?m[2].toLowerCase():''; if(suf==='k') val=Math.round(val*1000); if(suf==='m') val=Math.round(val*1000000); if(suf==='mil') val=Math.round(val*1000); if(suf==='millones'||suf==='millon'||suf==='millón') val=Math.round(val*1000000); return Number.isFinite(val)?val:null; }\n"
                                            "  function grabText(el){ if(!el) return ''; return el.textContent||el.innerText||el.getAttribute('title')||el.getAttribute('aria-label')||''; }\n"
                                            "  let fullName=null; const mt=document.querySelector('meta[property=\"og:title\"]'); if(mt){ const t=mt.getAttribute('content')||''; const mm=t.match(/^(.+?)\\s\\(@/); if(mm) fullName=mm[1].trim(); } if(!fullName){ const nameEl=document.querySelector('header h1, header h2'); if(nameEl){ const nt=grabText(nameEl).trim(); if(nt) fullName=nt; } }\n"
                                            "  let followers=null, following=null; const md=document.querySelector('meta[property=\"og:description\"]'); if(md){ const t=md.getAttribute('content')||''; const mf=t.match(/([0-9.,]+)\\s*(followers|seguidores)/i); const mg=t.match(/([0-9.,]+)\\s*(following|seguidos)/i); if(mf) followers=parseNum(mf[1]); if(mg) following=parseNum(mg[1]); }\n"
                                            "  if(followers===null){ const aF=document.querySelector('header section ul li a[href$=\"/followers/\"]'); if(aF){ const v=parseNum(grabText(aF)); if(v!==null) followers=v; } }\n"
                                            "  if(following===null){ const aG=document.querySelector('header section ul li a[href$=\"/following/\"]'); if(aG){ const v=parseNum(grabText(aG)); if(v!==null) following=v; } }\n"
                                            "  let biography=''; const bioCandidates=Array.from(document.querySelectorAll('[data-testid=\"user-bio\"], header section div, header section p')); for(const el of bioCandidates){ const txt=grabText(el).trim(); if(txt && !/[0-9.,]+\\s*(followers|seguidores|following|seguidos)/i.test(txt) && txt.length>=8){ biography=txt; break; } }\n"
                                            "  return { full_name: fullName, biography, followers, following };\n"
                                            "})()"
                                        )
                                        used_dom = True
                                        if not it.get("full_name") and dom_vals.get("full_name"):
                                            it["full_name"] = dom_vals.get("full_name")
                                        if (it.get("followers") is None) and (dom_vals.get("followers") is not None):
                                            it["followers"] = dom_vals.get("followers")
                                        if (it.get("following") is None) and (dom_vals.get("following") is not None):
                                            it["following"] = dom_vals.get("following")
                                        if not it.get("biography") and dom_vals.get("biography"):
                                            it["biography"] = dom_vals.get("biography")
                                    except Exception:
                                        pass
                            after = {
                                "full_name": it.get("full_name"),
                                "biography": it.get("biography"),
                                "followers": it.get("followers"),
                                "following": it.get("following"),
                            }
                            logger.info(
                                "%s | API=%s | HTML=%s | DOM=%s | final: nombre=%s, seguidores=%s, seguidos=%s",
                                uname,
                                str(before),
                                str(used_html),
                                str(used_dom),
                                after.get("full_name"),
                                str(after.get("followers")),
                                str(after.get("following")),
                            )
                            enriched.append(it)
                        result["following_details"] = enriched
                        logger.info("Count (seguidos del perfil): %s", str(result.get("following_count")))
                        return result
                    except Exception:
                        pass
                # Fallback UI o modo forzado por flag
                    # Fallback UI: abrir modal de seguidos y scrollear para recolectar usernames
                    page.goto(f"https://www.instagram.com/{username}/", timeout=30000)
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
                    page.wait_for_selector("a[href$='/following/']", timeout=20000)
                    page.locator("a[href$='/following/']").first.click()
                    # En algunas variantes de UI, el listado abre un modal (div[role='dialog']) y en otras, navega a una página completa.
                    # Esperamos a que aparezca el diálogo o, si no, continuamos con la página.
                    try:
                        page.wait_for_selector("div[role='dialog']", timeout=8000)
                    except Exception:
                        pass
                    usernames: List[str] = []
                    last_len = -1
                    unchanged_rounds = 0
                    while len(usernames) < limit:
                        try:
                            page.evaluate(
                                "(() => {\n"
                                "  const dlg = document.querySelector('div[role=\"dialog\"]');\n"
                                "  if (dlg) {\n"
                                "    const nodes = [dlg, ...Array.from(dlg.querySelectorAll('*'))];\n"
                                "    const sc = nodes.find(n => (n.scrollHeight||0) > (n.clientHeight||0));\n"
                                "    if (sc) { sc.scrollTop = sc.scrollHeight; return true; }\n"
                                "  }\n"
                                "  // Fallback: página completa de /following/\n"
                                "  const se = document.scrollingElement || document.documentElement;\n"
                                "  if (se) { se.scrollTop = se.scrollHeight; return true; }\n"
                                "  window.scrollTo(0, document.body.scrollHeight);\n"
                                "  return true;\n"
                                "})()"
                            )
                        except Exception:
                            page.mouse.wheel(0, 3000)
                        page.wait_for_timeout(800)

                        try:
                            found = page.evaluate(
                                "(() => {\n"
                                "  const dlg = document.querySelector('div[role=\"dialog\"]');\n"
                                "  const base = dlg || document;\n"
                                "  const anchors = Array.from(base.querySelectorAll('a[href^=\"/\"][href$=\"/\"], a[role=\"link\"][href^=\"/\"][href$=\"/\"]'));\n"
                                "  const invalid = new Set(['p','reels','stories','explore','accounts']);\n"
                                "  const out = [];\n"
                                "  for (const a of anchors) {\n"
                                "    const href = a.getAttribute('href') || '';\n"
                                "    const m = href.match(/^\\/([A-Za-z0-9._]+)\\/$/);\n"
                                "    if (m) { const seg = m[1]; if (!invalid.has(seg)) out.push(seg); }\n"
                                "  }\n"
                                "  return Array.from(new Set(out));\n"
                                "})()"
                            )
                        except Exception:
                            found = []
                        # Extra: intenta capturar nombres visibles en el diálogo para usar como fallback de full_name
                        try:
                            dialog_names = page.evaluate(
                                "(() => {\n"
                                "  const dlg = document.querySelector('div[role=\"dialog\"]');\n"
                                "  const base = dlg || document;\n"
                                "  const anchors = Array.from(base.querySelectorAll('a[href^=\"/\"][href$=\"/\"]'));\n"
                                "  const map = {};\n"
                                "  for (const a of anchors){\n"
                                "    const href = a.getAttribute('href')||'';\n"
                                "    const m = href.match(/^\\/([A-Za-z0-9._]+)\\/$/);\n"
                                "    if (!m) continue;\n"
                                "    const uname = m[1];\n"
                                "    const container = a.closest('li, div') || a.parentElement;\n"
                                "    let txt = '';\n"
                                "    if (container){ txt = (container.textContent||'').trim(); }\n"
                                "    // Limpia etiquetas de botones y estados\n"
                                "    txt = txt.replace(/Seguir|Siguiendo|Follow|Following|Message|Mensaje/gi, '').trim();\n"
                                "    // Busca la primera línea que parezca nombre (contiene espacios y letras)\n"
                                "    const lines = txt.split(/\n+/).map(s => s.trim()).filter(Boolean);\n"
                                "    const cand = lines.find(s => /[A-Za-zÁÉÍÓÚáéíóúÑñ]+\s+[A-Za-zÁÉÍÓÚáéíóúÑñ]+/.test(s)) || lines[0] || '';\n"
                                "    if (cand && cand.length>=3) map[uname] = cand;\n"
                                "  }\n"
                                "  return map;\n"
                                "})()"
                            )
                        except Exception:
                            dialog_names = {}
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
                            break
                        page.wait_for_timeout(max(delay_ms, 1200))

                    out: List[Dict[str, Any]] = []
                    for uname in usernames[:limit]:
                        try:
                            logger.info("Procesando usuario desde UI: %s", uname)
                        except Exception:
                            pass
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
                            "  const r3 = await fetchRetry('https://www.instagram.com/api/v1/users/web_profile_info/?username=' + encodeURIComponent(u), { headers: h });\n"
                            "  const j3 = await r3.json();\n"
                            "  const udata = j3?.data?.user || {};\n"
                            "  const accType = (udata?.is_professional ? (udata?.is_business_account ? 'empresa' : 'creador') : 'personal');\n"
                            "  return { username: u, full_name: udata?.full_name ?? null, biography: udata?.biography ?? '', account_type: accType, category: udata?.category_name ?? null, followers: udata?.edge_followed_by?.count ?? null, following: udata?.edge_follow?.count ?? null, url: location.origin + '/' + u + '/' };\n"
                            ")('" + uname + "', " + str(retry_tries) + ", " + str(retry_base_ms) + ")"
                        )
                        try:
                            item = page.evaluate(jscode)
                            # Fallback inmediato: si falta full_name y tenemos nombre del diálogo, úsalo
                            try:
                                if (not item.get("full_name")) and dialog_names.get(uname):
                                    item["full_name"] = dialog_names.get(uname)
                            except Exception:
                                pass
                            # Si el API devuelve campos críticos vacíos, intenta fallback HTML y fusiona.
                            needs_fb = (item.get("followers") is None and item.get("following") is None) or (not item.get("full_name"))
                            if needs_fb:
                                fb_js = (
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
                                    "  const h = { 'x-requested-with': 'XMLHttpRequest', 'referer': location.origin + '/' };\n"
                                    "  const r = await fetchRetry(location.origin + '/' + u + '/', { headers: h });\n"
                                    "  const html = await r.text();\n"
                                    "  const doc = new DOMParser().parseFromString(html, 'text/html');\n"
                                    "  function parseNum(txt){\n"
                                    "    if (!txt) return null;\n"
                                    "    const t = String(txt).trim();\n"
                                    "    const m = t.match(/([0-9.,]+)\\s*(k|m|K|M|mil|millones|millon|millón)?/i);\n"
                                    "    if (!m) return null;\n"
                                    "    let n = m[1].replace(/\\s/g,'');\n"
                                    "    n = n.replace(/\\.(?=\\d{3}\\b)/g,'');\n"
                                    "    n = n.replace(/,(?=\\d{3}\\b)/g,'');\n"
                                    "    let val = Number(n.replace(',', '.'));\n"
                                    "    const suf = m[2] ? m[2].toLowerCase() : '';\n"
                                    "    if (suf==='k') val = Math.round(val*1000);\n"
                                    "    if (suf==='m') val = Math.round(val*1000000);\n"
                                    "    if (suf==='mil') val = Math.round(val*1000);\n"
                                    "    if (suf==='millones' || suf==='millon' || suf==='millón') val = Math.round(val*1000000);\n"
                                    "    return Number.isFinite(val) ? val : null;\n"
                                    "  }\n"
                                    "  let fullName = null;\n"
                                    "  const metaTitle = doc.querySelector('meta[property=\"og:title\"]');\n"
                                    "  if (metaTitle) {\n"
                                    "    const t = metaTitle.getAttribute('content') || '';\n"
                                    "    const mt = t.match(/^(.+?)\\s\\(@/);\n"
                                    "    if (mt) fullName = mt[1].trim();\n"
                                    "  }\n"
                                    "  let followers = null, following = null;\n"
                                    "  const mdesc = doc.querySelector('meta[property=\"og:description\"]');\n"
                                    "  if (mdesc) {\n"
                                    "    const t = mdesc.getAttribute('content') || '';\n"
                                    "    const reF = /([0-9.,]+)\\s*(followers|seguidores)/i;\n"
                                    "    const reFg = /([0-9.,]+)\\s*(following|seguidos)/i;\n"
                                    "    const mf = t.match(reF);\n"
                                    "    const mg = t.match(reFg);\n"
                                    "    if (mf) followers = parseNum(mf[1]);\n"
                                    "    if (mg) following = parseNum(mg[1]);\n"
                                    "  }\n"
                                    "  return { full_name: fullName, followers: followers, following: following };\n"
                                    ")('" + uname + "', " + str(retry_tries) + ", " + str(retry_base_ms) + ")"
                                )
                                try:
                                    fb = page.evaluate(fb_js)
                                    # Fusiona solo si aporta datos
                                    if not item.get("full_name") and fb.get("full_name"):
                                        item["full_name"] = fb.get("full_name")
                                    if item.get("followers") is None and fb.get("followers") is not None:
                                        item["followers"] = fb.get("followers")
                                    if item.get("following") is None and fb.get("following") is not None:
                                        item["following"] = fb.get("following")
                                except Exception:
                                    pass
                                # Fallback final: navegar al perfil y leer del DOM si aún faltan datos
                                if (item.get("followers") is None or item.get("following") is None) or (not item.get("full_name")):
                                    try:
                                        page.goto(f"https://www.instagram.com/{uname}/", timeout=30000)
                                        # Intenta cerrar/aceptar cookies en el perfil si aparecen
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
                                        page.wait_for_load_state("domcontentloaded")
                                        try:
                                            page.wait_for_selector(
                                                "meta[property='og:description'], header section ul li a[href$='/followers/'], header section ul li a[href$='/following/']",
                                                timeout=12000,
                                            )
                                        except Exception:
                                            pass
                                        page.wait_for_timeout(900)
                                        dom_vals = page.evaluate(
                                            "(() => {\n"
                                            "  function parseNum(txt){\n"
                                            "    if (!txt) return null;\n"
                                            "    const t = String(txt).trim();\n"
                                            "    const m = t.match(/([0-9.,]+)\\s*(k|m|K|M|mil|millones|millon|millón)?/i);\n"
                                            "    if (!m) return null;\n"
                                            "    let n = m[1].replace(/\\s/g,'');\n"
                                            "    n = n.replace(/\\.(?=\\d{3}\\b)/g,'');\n"
                                            "    n = n.replace(/,(?=\\d{3}\\b)/g,'');\n"
                                            "    let val = Number(n.replace(',', '.'));\n"
                                            "    const suf = m[2] ? m[2].toLowerCase() : '';\n"
                                            "    if (suf==='k') val = Math.round(val*1000);\n"
                                            "    if (suf==='m') val = Math.round(val*1000000);\n"
                                            "    if (suf==='mil') val = Math.round(val*1000);\n"
                                            "    if (suf==='millones' || suf==='millon' || suf==='millón') val = Math.round(val*1000000);\n"
                                            "    return Number.isFinite(val) ? val : null;\n"
                                            "  }\n"
                                            "  function grabText(el){\n"
                                            "    if (!el) return '';\n"
                                            "    return el.textContent || el.innerText || el.getAttribute('title') || el.getAttribute('aria-label') || '';\n"
                                            "  }\n"
                                            "  let fullName = null;\n"
                                            "  const mt = document.querySelector('meta[property=\"og:title\"]');\n"
                                            "  if (mt) { const t = mt.getAttribute('content')||''; const mmm = t.match(/^(.+?)\\s\\(@/); if (mmm) fullName = mmm[1].trim(); }\n"
                                            "  if (!fullName){ const nameEl = document.querySelector('header h1, header h2'); if (nameEl){ const nt = grabText(nameEl).trim(); if (nt) fullName = nt; } }\n"
                                            "  let followers = null, following = null;\n"
                                            "  const mdesc = document.querySelector('meta[property=\"og:description\"]');\n"
                                            "  if (mdesc) {\n"
                                            "    const t = mdesc.getAttribute('content')||'';\n"
                                            "    const reF = /([0-9.,]+)\\s*(followers|seguidores)/i;\n"
                                            "    const reFg = /([0-9.,]+)\\s*(following|seguidos)/i;\n"
                                            "    const mf = t.match(reF); const mg = t.match(reFg);\n"
                                            "    if (mf) followers = parseNum(mf[1]);\n"
                                            "    if (mg) following = parseNum(mg[1]);\n"
                                            "  }\n"
                                            "  if (followers===null){\n"
                                            "    const selFStr = `a[href$='/followers/'] span[title], a[href$='/followers/'] span, a[href$='/followers/'] div, header section ul li a[href$='/followers/'] span[title], header section ul li a[href$='/followers/'] span, li a[href$='/followers/'], header section ul li a[href$='/followers/']`;\n"
                                            "    const selsF = selFStr.split(/\\s*,\\s*/);\n"
                                            "    for (const s of selsF){ const el = document.querySelector(s); if (el){ const v = parseNum(grabText(el)); if (v!==null){ followers = v; break; } } }\n"
                                            "    if (followers===null){ const aF = document.querySelector('header section ul li a[href$=\"/followers/\"]'); if (aF){ const v = parseNum(grabText(aF)); if (v!==null) followers = v; } }\n"
                                            "  }\n"
                                            "  if (following===null){\n"
                                            "    const selFgStr = `a[href$='/following/'] span[title], a[href$='/following/'] span, a[href$='/following/'] div, header section ul li a[href$='/following/'] span[title], header section ul li a[href$='/following/'] span, li a[href$='/following/'], header section ul li a[href$='/following/']`;\n"
                                            "    const selsFg = selFgStr.split(/\\s*,\\s*/);\n"
                                            "    for (const s of selsFg){ const el = document.querySelector(s); if (el){ const v = parseNum(grabText(el)); if (v!==null){ following = v; break; } } }\n"
                                            "    if (following===null){ const aFg = document.querySelector('header section ul li a[href$=\"/following/\"]'); if (aFg){ const v = parseNum(grabText(aFg)); if (v!==null) following = v; } }\n"
                                            "  }\n"
                                            "  let biography = '';\n"
                                            "  const bioCandidates = Array.from(document.querySelectorAll('[data-testid=\"user-bio\"], header section div, header section p'));\n"
                                            "  for (const el of bioCandidates){\n"
                                            "    const txt = grabText(el).trim();\n"
                                            "    if (txt && !/[0-9.,]+\\s*(followers|seguidores|following|seguidos)/i.test(txt) && txt.length >= 8){ biography = txt; break; }\n"
                                            "  }\n"
                                            "  return { full_name: fullName, biography, followers, following };\n"
                                            "})()"
                                        )
                                        if not item.get("full_name") and dom_vals.get("full_name"):
                                            item["full_name"] = dom_vals.get("full_name")
                                        if item.get("followers") is None and dom_vals.get("followers") is not None:
                                            item["followers"] = dom_vals.get("followers")
                                        if item.get("following") is None and dom_vals.get("following") is not None:
                                            item["following"] = dom_vals.get("following")
                                    except Exception:
                                        pass
                            # Log final por usuario en modo UI
                            try:
                                logger.info(
                                    "[UI] %s | nombre='%s' | bio_len=%s | seguidores=%s | seguidos=%s",
                                    uname,
                                    item.get("full_name") or "",
                                    len(item.get("biography") or ""),
                                    str(item.get("followers")),
                                    str(item.get("following")),
                                )
                            except Exception:
                                pass
                            out.append(item)
                        except Exception:
                            # Fallback: lee la página del perfil y extrae conteos desde og:description y nombre desde og:title;
                            # además, si aún faltan datos, navega al DOM del perfil y completa.
                            fb_js = (
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
                                "  const h = { 'x-requested-with': 'XMLHttpRequest', 'referer': location.origin + '/' };\n"
                                "  const r = await fetchRetry(location.origin + '/' + u + '/', { headers: h });\n"
                                "  const html = await r.text();\n"
                                "  const doc = new DOMParser().parseFromString(html, 'text/html');\n"
                                "  function parseNum(txt){\n"
                                "    if (!txt) return null;\n"
                                "    const t = String(txt).trim();\n"
                                "    const m = t.match(/([0-9.,]+)\\s*(k|m|K|M|mil|millones|millon|millón)?/i);\n"
                                "    if (!m) return null;\n"
                                "    let n = m[1].replace(/\\s/g,'');\n"
                                "    n = n.replace(/\\.(?=\\d{3}\\b)/g,'');\n"
                                "    n = n.replace(/,(?=\\d{3}\\b)/g,'');\n"
                                "    let val = Number(n.replace(',', '.'));\n"
                                "    const suf = m[2] ? m[2].toLowerCase() : '';\n"
                                "    if (suf==='k') val = Math.round(val*1000);\n"
                                "    if (suf==='m') val = Math.round(val*1000000);\n"
                                "    if (suf==='mil') val = Math.round(val*1000);\n"
                                "    if (suf==='millones' || suf==='millon' || suf==='millón') val = Math.round(val*1000000);\n"
                                "    return Number.isFinite(val) ? val : null;\n"
                                "  }\n"
                                "  let fullName = null;\n"
                                "  const metaTitle = doc.querySelector('meta[property=\"og:title\"]');\n"
                                "  if (metaTitle) {\n"
                                "    const t = metaTitle.getAttribute('content') || '';\n"
                                "    const mt = t.match(/^(.+?)\\s\\(@/);\n"
                                "    if (mt) fullName = mt[1].trim();\n"
                                "  }\n"
                                "  let followers = null, following = null;\n"
                                "  const mdesc = doc.querySelector('meta[property=\"og:description\"]');\n"
                                "  if (mdesc) {\n"
                                "    const t = mdesc.getAttribute('content') || '';\n"
                                "    const reF = /([0-9.,]+)\\s*(followers|seguidores)/i;\n"
                                "    const reFg = /([0-9.,]+)\\s*(following|seguidos)/i;\n"
                                "    const mf = t.match(reF);\n"
                                "    const mg = t.match(reFg);\n"
                                "    if (mf) followers = parseNum(mf[1]);\n"
                                "    if (mg) following = parseNum(mg[1]);\n"
                                "  }\n"
                                "  return { username: u, full_name: fullName, biography: '', account_type: null, category: null, followers: followers, following: following, url: location.origin + '/' + u + '/' };\n"
                                ")('" + uname + "', " + str(retry_tries) + ", " + str(retry_base_ms) + ")"
                            )
                            try:
                                fb_item = page.evaluate(fb_js)
                                # Si aún faltan datos críticos, navega al perfil y raspa del DOM
                                if (fb_item.get("followers") is None or fb_item.get("following") is None or not fb_item.get("full_name")):
                                    try:
                                        page.goto(f"https://www.instagram.com/{uname}/", timeout=30000)
                                        page.wait_for_load_state("domcontentloaded")
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
                                        page.wait_for_timeout(800)
                                        dom_vals = page.evaluate(
                                            "(() => {\n"
                                            "  function parseNum(txt){ if (!txt) return null; const t=String(txt).trim(); const m=t.match(/([0-9.,]+)\\s*(k|m|K|M|mil|millones|millon|millón)?/i); if(!m) return null; let n=m[1].replace(/\\s/g,''); n=n.replace(/\\.(?=\\d{3}\\b)/g,''); n=n.replace(/,(?=\\d{3}\\b)/g,''); let val=Number(n.replace(',', '.')); const suf=m[2]?m[2].toLowerCase():''; if(suf==='k') val=Math.round(val*1000); if(suf==='m') val=Math.round(val*1000000); if(suf==='mil') val=Math.round(val*1000); if(suf==='millones'||suf==='millon'||suf==='millón') val=Math.round(val*1000000); return Number.isFinite(val)?val:null; }\n"
                                            "  function grabText(el){ if(!el) return ''; return el.textContent||el.innerText||el.getAttribute('title')||el.getAttribute('aria-label')||''; }\n"
                                            "  let fullName=null; const mt=document.querySelector('meta[property=\"og:title\"]'); if(mt){ const t=mt.getAttribute('content')||''; const mm=t.match(/^(.+?)\\s\\(@/); if(mm) fullName=mm[1].trim(); } if(!fullName){ const nameEl=document.querySelector('header h1, header h2'); if(nameEl){ const nt=grabText(nameEl).trim(); if(nt) fullName=nt; } }\n"
                                            "  let followers=null, following=null; const md=document.querySelector('meta[property=\"og:description\"]'); if(md){ const t=md.getAttribute('content')||''; const mf=t.match(/([0-9.,]+)\\s*(followers|seguidores)/i); const mg=t.match(/([0-9.,]+)\\s*(following|seguidos)/i); if(mf) followers=parseNum(mf[1]); if(mg) following=parseNum(mg[1]); }\n"
                                            "  if(followers===null){ const aF=document.querySelector('header section ul li a[href$=\"/followers/\"]'); if(aF){ const v=parseNum(grabText(aF)); if(v!==null) followers=v; } }\n"
                                            "  if(following===null){ const aG=document.querySelector('header section ul li a[href$=\"/following/\"]'); if(aG){ const v=parseNum(grabText(aG)); if(v!==null) following=v; } }\n"
                                            "  let biography=''; const bioCandidates=Array.from(document.querySelectorAll('[data-testid=\"user-bio\"], header section div, header section p')); for(const el of bioCandidates){ const txt=grabText(el).trim(); if(txt && !/[0-9.,]+\\s*(followers|seguidores|following|seguidos)/i.test(txt) && txt.length>=8){ biography=txt; break; } }\n"
                                            "  return { full_name: fullName, biography, followers, following };\n"
                                            "})()"
                                        )
                                        if not fb_item.get("full_name") and dom_vals.get("full_name"):
                                            fb_item["full_name"] = dom_vals.get("full_name")
                                        if fb_item.get("followers") is None and dom_vals.get("followers") is not None:
                                            fb_item["followers"] = dom_vals.get("followers")
                                        if fb_item.get("following") is None and dom_vals.get("following") is not None:
                                            fb_item["following"] = dom_vals.get("following")
                                    except Exception:
                                        pass
                                try:
                                    logger.info(
                                        "[UI-fallback] %s | nombre='%s' | seguidores=%s | seguidos=%s",
                                        uname,
                                        fb_item.get("full_name") or "",
                                        str(fb_item.get("followers")),
                                        str(fb_item.get("following")),
                                    )
                                except Exception:
                                    pass
                                out.append(fb_item)
                            except Exception:
                                out.append({"username": uname, "full_name": None, "biography": "", "account_type": None, "category": None, "followers": None, "following": None, "url": f"https://www.instagram.com/{uname}/"})
                    return {"username": username, "following_count": None, "following_details": out}
            except Exception as e:
                logger.error("Fallo inesperado en get_following_details: %s", e)
                return {"username": username, "following_count": None, "following_details": []}
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
                    # Fallback UI: abrir modal de seguidos y scrollear para recolectar usernames
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
    const m = t.match(/([0-9.,]+)\\s*(k|m|K|M|mil|millones|millon|millón)?/i);
    if (!m) return null;
    let n = m[1].replace(/\\s/g,'');
    n = n.replace(/\\.(?=\\d{3}\\b)/g,'');
    n = n.replace(/,(?=\\d{3}\\b)/g,'');
    let val = Number(n.replace(',', '.'));
    const suf = m[2] ? m[2].toLowerCase() : '';
    if (suf==='k') val = Math.round(val*1000);
    if (suf==='m') val = Math.round(val*1000000);
    if (suf==='mil') val = Math.round(val*1000);
    if (suf==='millones' || suf==='millon' || suf==='millón') val = Math.round(val*1000000);
    return Number.isFinite(val) ? val : null;
  }
  const selStr = `a[href$='/followers/'] span, a[href$='/followers/'] div, header section ul li a[href$='/followers/']`;
  const selectors = selStr.split(/\\s*,\\s*/);
  for (const sel of selectors){
    const el = document.querySelector(sel);
    if (el){
      const v = parseNum(el.textContent||el.innerText||'');
      if (v!==null) return v;
    }
  }
  const m = document.querySelector('meta[property=\"og:description\"]');
  const t = m ? (m.getAttribute('content')||'') : '';
  const re = /([0-9.,]+)\\s*(followers|seguidores)/i;
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
