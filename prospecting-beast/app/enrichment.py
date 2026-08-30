"""Public-web enrichment helpers.

No login scraping and no message delivery. Email checks are DNS/SMTP handshake
signals only; servers may intentionally return ambiguous results.
"""
from __future__ import annotations
import asyncio, random, re, smtplib
from email.utils import parseaddr
from urllib.parse import urljoin, urlparse
import aiohttp
from bs4 import BeautifulSoup
import dns.asyncresolver

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

def email_permutations(first_name: str, last_name: str, domain: str) -> list[str]:
    f = re.sub(r"[^a-z0-9]", "", (first_name or "").lower())
    l = re.sub(r"[^a-z0-9]", "", (last_name or "").lower())
    d = re.sub(r"^@", "", (domain or "").lower().strip())
    if not f or not l or not d:
        return []
    locals_ = [
        f"{f}.{l}", f"{f[0]}{l}", f"{f}{l[0]}", f"{f}{l}",
        f"{l}.{f}", f"{l}{f[0]}", f"{l[0]}{f}", f"{f}_{l}",
        f"{f}-{l}", f"{l}_{f}", f"{l}-{f}", f"{f}", f"{l}",
        f"{f[0]}.{l}", f"{f}.{l[0]}",
    ]
    return list(dict.fromkeys(f"{x}@{d}" for x in locals_))[:15]

async def mx_hosts(domain: str) -> list[str]:
    try:
        answers = await dns.asyncresolver.resolve(domain, "MX")
        return sorted({str(a.exchange).rstrip(".") for a in answers})
    except Exception:
        return []

async def smtp_rcpt_signal(address: str, mx_host: str, timeout: float = 8.0) -> str:
    """Return accepted/rejected/unknown without sending DATA or a message."""
    def _probe():
        try:
            with smtplib.SMTP(timeout=timeout) as smtp:
                smtp.ehlo_or_helo_if_needed()
                code, _ = smtp.mail("probe@invalid.local")
                if code >= 400:
                    return "unknown"
                code, _ = smtp.rcpt(address)
                smtp.rset()
                if 200 <= code < 300:
                    return "accepted"
                if 500 <= code < 600:
                    return "rejected"
                return "unknown"
        except Exception:
            return "unknown"
    return await asyncio.to_thread(lambda: _probe())

async def detect_catch_all(domain: str, mx: list[str] | None = None) -> dict:
    mx = mx or await mx_hosts(domain)
    if not mx:
        return {"mx": [], "mx_ok": False, "catch_all": False, "catch_all_confident": False, "signal": "no_mx"}
    fake = f"random_{random.randrange(100000, 999999)}@{domain.lower().lstrip('@')}"
    for host in mx[:2]:
        signal = await smtp_rcpt_signal(fake, host)
        if signal == "accepted":
            return {"mx": mx, "mx_ok": True, "catch_all": True, "catch_all_confident": True, "signal": "accepted", "test_address": fake}
        if signal == "rejected":
            return {"mx": mx, "mx_ok": True, "catch_all": False, "catch_all_confident": True, "signal": "rejected", "test_address": fake}
    return {"mx": mx, "mx_ok": True, "catch_all": None, "catch_all_confident": False, "signal": "unknown", "test_address": fake}

async def scrape_public_emails(urls: list[str], *, max_urls: int = 5, timeout: float = 15.0) -> dict:
    urls = list(dict.fromkeys((u or "").strip() for u in urls if (u or "").strip()))[:max_urls]
    headers = {"User-Agent": "ProspectingBeast/32.0 (+public-web-enrichment)"}
    found, pages = set(), []
    timeout_cfg = aiohttp.ClientTimeout(total=timeout)
    async with aiohttp.ClientSession(timeout=timeout_cfg, headers=headers) as session:
        for raw in urls:
            try:
                p = urlparse(raw if "://" in raw else "https://" + raw)
                if not p.hostname or "linkedin.com" in p.hostname.lower():
                    continue
                async with session.get(raw, allow_redirects=True) as r:
                    if r.status >= 400 or "html" not in (r.headers.get("content-type") or "").lower():
                        continue
                    html = await r.text(errors="ignore")
                soup = BeautifulSoup(html, "html.parser")
                for a in soup.select('a[href^="mailto:"]'):
                    addr = parseaddr(a.get("href", "")[7:].split("?",1)[0])[1].lower()
                    if addr:
                        found.add(addr)
                for s in soup.stripped_strings:
                    found.update(x.lower() for x in EMAIL_RE.findall(s))
                pages.append({"url": str(r.url), "emails": sorted(found)})
            except Exception as exc:
                pages.append({"url": raw, "error": str(exc), "emails": []})
    return {"emails": sorted(found), "pages": pages}
