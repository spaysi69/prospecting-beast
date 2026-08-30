"""Public-web contact enrichment helpers.

This module is intentionally limited to public pages and DNS/SMTP validation.
It does not scrape authenticated social networks, bypass CAPTCHAs, or send mail.
"""
from __future__ import annotations

import asyncio
import random
import re
import smtplib
import socket
from email.utils import parseaddr
from typing import Iterable
from urllib.parse import urlparse

import aiohttp
from bs4 import BeautifulSoup
import dns.asyncresolver

UA = "ProspectingBeast/32.0 (+public-web-enrichment)"
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def email_permutations(first_name: str, last_name: str, domain: str) -> list[str]:
    first = re.sub(r"[^a-z0-9]", "", (first_name or "").lower())
    last = re.sub(r"[^a-z0-9]", "", (last_name or "").lower())
    domain = (domain or "").lower().strip().lstrip("@")
    if not first or not last or not domain:
        return []
    fi, li = first[0], last[0]
    locals_ = [
        f"{first}.{last}", f"{first}_{last}", f"{first}-{last}", f"{first}{last}",
        f"{first}{li}", f"{fi}{last}", f"{fi}.{last}", f"{fi}_{last}", f"{last}.{first}",
        f"{last}.{fi}", f"{last}{fi}", f"{last}{first}", f"{first}", f"{last}", f"{fi}{li}",
    ]
    return list(dict.fromkeys(f"{local}@{domain}" for local in locals_))


def valid_email(address: str) -> bool:
    return bool(parseaddr(address or "")[1] and EMAIL_RE.fullmatch(parseaddr(address or "")[1]))


async def mx_hosts(domain: str) -> list[str]:
    resolver = dns.asyncresolver.Resolver()
    answers = await resolver.resolve(domain, "MX")
    return sorted({str(a.exchange).rstrip(".") for a in answers})


def _smtp_probe_sync(host: str, sender: str, recipient: str, timeout: float) -> tuple[bool, int, str]:
    code = 0
    msg = ""
    with smtplib.SMTP(host, 25, timeout=timeout) as smtp:
        smtp.ehlo_or_helo_if_needed()
        smtp.mail(sender)
        code, msg_bytes = smtp.rcpt(recipient)
        msg = msg_bytes.decode(errors="ignore") if isinstance(msg_bytes, (bytes, bytearray)) else str(msg_bytes)
        try:
            smtp.rset()
        except Exception:
            pass
    return (200 <= code < 300, code, msg)


async def detect_catch_all(domain: str, *, timeout: float = 8.0) -> dict:
    """Probe one random recipient without sending a message.

    A positive RCPT response is treated as a catch-all *signal*, not proof; some
    providers intentionally accept recipients and filter later.
    """
    try:
        mx = await mx_hosts(domain)
    except Exception as exc:
        return {"domain": domain, "mx": [], "catch_all": False, "confidence_reduced": False, "error": str(exc)}
    if not mx:
        return {"domain": domain, "mx": [], "catch_all": False, "confidence_reduced": False, "error": "No MX records"}
    fake = f"random_{random.randint(10**6, 10**9)}@{domain}"
    sender = f"probe_{random.randint(10**6, 10**9)}@{domain}"
    for host in mx[:2]:
        try:
            ok, code, msg = await asyncio.to_thread(_smtp_probe_sync, host, sender, fake, timeout)
            if ok:
                return {"domain": domain, "mx": mx, "catch_all": True, "confidence_reduced": True, "smtp_code": code, "smtp_message": msg}
            if code in (550, 551, 553):
                return {"domain": domain, "mx": mx, "catch_all": False, "confidence_reduced": False, "smtp_code": code, "smtp_message": msg}
        except (OSError, socket.timeout, smtplib.SMTPException) as exc:
            last = str(exc)
            continue
    return {"domain": domain, "mx": mx, "catch_all": None, "confidence_reduced": False, "error": locals().get("last", "SMTP probe inconclusive")}


async def scrape_public_emails(urls: Iterable[str], *, max_pages: int = 10) -> dict:
    """Fetch public HTML pages and extract visible email addresses only."""
    found: set[str] = set()
    pages: list[dict] = []
    timeout = aiohttp.ClientTimeout(total=20)
    headers = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml"}
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        for raw_url in list(urls)[:max_pages]:
            url = (raw_url or "").strip()
            if not url:
                continue
            if "linkedin.com" in (urlparse(url).hostname or "").lower():
                pages.append({"url": url, "ok": False, "error": "Authenticated social-network crawling is disabled."})
                continue
            try:
                async with session.get(url, allow_redirects=True) as resp:
                    if resp.status >= 400:
                        raise RuntimeError(f"HTTP {resp.status}")
                    if "html" not in (resp.headers.get("content-type") or ""):
                        raise RuntimeError("not HTML")
                    html = await resp.text(errors="ignore")
                soup = BeautifulSoup(html, "html.parser")
                text = " ".join(soup.stripped_strings)
                page_emails = {e.lower().strip(".,;:()[]{}<>") for e in EMAIL_RE.findall(text)}
                found.update(e for e in page_emails if valid_email(e))
                pages.append({"url": url, "ok": True, "emails": sorted(page_emails)})
            except Exception as exc:
                pages.append({"url": url, "ok": False, "error": str(exc)})
    return {"emails": sorted(found), "pages": pages}


async def enrich_public_contact(first_name: str, last_name: str, domain: str, public_urls: Iterable[str] = ()) -> dict:
    domain = (domain or "").lower().strip().lstrip("www.")
    candidates = email_permutations(first_name, last_name, domain)
    scraped = await scrape_public_emails(public_urls)
    catch_all = await detect_catch_all(domain)
    public_matches = [e for e in scraped["emails"] if e.endswith("@" + domain)]
    confidence_multiplier = 0.5 if catch_all.get("catch_all") else 1.0
    return {
        "domain": domain,
        "candidates": [{"email": e, "confidence": 0.5 * confidence_multiplier} for e in candidates],
        "public_matches": [{"email": e, "confidence": 0.95} for e in public_matches],
        "mx": catch_all.get("mx", []),
        "catch_all": catch_all.get("catch_all"),
        "confidence_multiplier": confidence_multiplier,
        "pages": scraped.get("pages", []),
    }
