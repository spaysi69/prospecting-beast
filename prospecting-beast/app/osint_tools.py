"""Passive OSINT helper utilities used by Prospecting Beast.

This module deliberately avoids authenticated-platform scraping, CAPTCHA bypasses,
proxy/IP rotation, or anti-bot evasion. It is designed for public, permitted sources.
"""
from __future__ import annotations

import asyncio
import json
import re
import socket
import urllib.robotparser
from dataclasses import dataclass
from email.utils import parseaddr
from typing import Iterable
from urllib.parse import quote_plus, urlparse

import aiohttp
from bs4 import BeautifulSoup
import dns.asyncresolver


@dataclass
class PublicPage:
    url: str
    title: str
    text: str
    json_ld: list[dict]


def _json_ld(soup: BeautifulSoup) -> list[dict]:
    out: list[dict] = []
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = tag.string or tag.get_text(" ", strip=True)
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if isinstance(item, dict):
                out.append(item)
    return out


def _allowed_by_robots(url: str, user_agent: str) -> bool:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return False
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    rp = urllib.robotparser.RobotFileParser(robots_url)
    try:
        rp.read()
        return rp.can_fetch(user_agent, url)
    except Exception:
        # If robots.txt cannot be read, do not guess around protections: allow a
        # single public-page request under normal rate limiting, but never bypass.
        return True


async def fetch_public_page(url: str, *, timeout: float = 20.0, user_agent: str = "ProspectingBeast/1.0") -> PublicPage:
    host = (urlparse(url).hostname or "").lower()
    if "linkedin.com" in host:
        raise RuntimeError("Direct LinkedIn profile crawling is disabled. Use publicly indexed search results or an approved LinkedIn API instead.")
    if not _allowed_by_robots(url, user_agent):
        raise RuntimeError("robots.txt disallows this page for the configured user agent")
    headers = {"User-Agent": user_agent, "Accept": "text/html,application/xhtml+xml"}
    timeout_cfg = aiohttp.ClientTimeout(total=timeout)
    async with aiohttp.ClientSession(timeout=timeout_cfg, headers=headers) as session:
        async with session.get(url, allow_redirects=True) as resp:
            if resp.status >= 400:
                raise RuntimeError(f"Public page fetch failed: HTTP {resp.status}")
            ctype = resp.headers.get("content-type", "")
            if "html" not in ctype:
                raise RuntimeError(f"Unsupported content type: {ctype}")
            html = await resp.text(errors="ignore")
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    ld = _json_ld(soup)
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    text = " ".join(soup.stripped_strings)
    return PublicPage(url=url, title=title, text=text[:25000], json_ld=ld)


def parse_public_people(page: PublicPage, allowed_domains: Iterable[str] | None = None) -> list[dict]:
    """Extract Person/Organization JSON-LD records from a public page.

    This is generic public-page parsing. It is not a LinkedIn scraper.
    """
    allowed = {d.lower().lstrip("www.") for d in (allowed_domains or []) if d}
    people: list[dict] = []
    for obj in page.json_ld:
        typ = obj.get("@type")
        types = {str(x).lower() for x in typ} if isinstance(typ, list) else {str(typ).lower()}
        if "person" not in types:
            continue
        works = obj.get("worksFor") or obj.get("worksfor") or {}
        if isinstance(works, list):
            works = works[0] if works else {}
        if isinstance(works, dict):
            org_name = works.get("name", "")
            same_as = works.get("sameAs", "")
        else:
            org_name, same_as = str(works), ""
        if allowed and same_as:
            try:
                host = urlparse(str(same_as)).hostname or ""
            except Exception:
                host = ""
            if host and host.lower().lstrip("www.") not in allowed:
                continue
        name = str(obj.get("name") or "").strip()
        title = str(obj.get("jobTitle") or "").strip()
        if name and title:
            address = obj.get("address") or {}
            people.append({
                "name": name,
                "title": title,
                "organization_name": org_name,
                "address": address if isinstance(address, dict) else str(address),
                "source_url": page.url,
            })
    # stable dedupe
    seen = set(); out = []
    for p in people:
        k = (p["name"].casefold(), p["title"].casefold(), p["source_url"])
        if k not in seen:
            seen.add(k); out.append(p)
    return out


def xray_matrix(company: str, domains: list[str], roles: list[str], location: str = "", industry: str = "") -> list[dict]:
    """Generate diverse, public-search X-ray queries.

    The matrix expands coverage via role families and search variants; it does not
    attempt to bypass a search provider's controls or pagination safeguards.
    """
    company_q = f'"{company}"' if company else ""
    location_q = f'"{location}"' if location else ""
    industry_q = f'"{industry}"' if industry else ""
    core = [f'"{r}"' for r in roles[:30]]
    role_groups = [
        ["IT Manager", "IT Director", "Head of IT", "VP of IT", "CIO", "CTO"],
        ["System Administrator", "Systems Administrator", "Network Manager", "Infrastructure Manager", "Head of Infrastructure"],
        ["Security Manager", "Cybersecurity Manager", "IT Security Manager", "Information Security", "CISO"],
        ["Cloud Manager", "Cloud Infrastructure", "Head of DevOps", "DevOps Manager", "Head of Data", "Transformation Manager"],
        ["IT Operations Manager", "Operations Manager", "Help Desk Manager", "End User Manager", "IT Admin"],
    ]
    queries: list[dict] = []
    seen = set()
    def add(q: str, family: str, engine: str = "web"):
        q = re.sub(r"\s+", " ", q).strip()
        if q and q not in seen:
            seen.add(q); queries.append({"query": q, "family": family, "engine": engine})
    for fam, group in zip(["leadership", "systems", "security", "cloud_devops_data", "operations"], role_groups):
        chosen = [r for r in group if not roles or any(r.casefold() in x.casefold() or x.casefold() in r.casefold() for x in roles)]
        terms = chosen or [r for r in group]
        ors = " OR ".join(f'"{r}"' for r in terms)
        suffix = " ".join(x for x in [location_q, industry_q] if x)
        add(f'site:linkedin.com/in {company_q} ({ors}) {suffix}', fam, "linkedin")
        for r in terms[:3]:
            add(f'site:linkedin.com/in {company_q} "{r}" {suffix}', fam, "linkedin")
        if domains:
            add(f'site:{domains[0]} ({ors}) {suffix}', fam, "official")
    if core:
        add(f'site:linkedin.com/in {company_q} ({" OR ".join(core[:8])}) {location_q}', "targeted", "linkedin")
    return queries


def email_variants(first_name: str, last_name: str, domain: str) -> list[str]:
    first = re.sub(r"[^a-z0-9]", "", first_name.lower())
    last = re.sub(r"[^a-z0-9]", "", last_name.lower())
    domain = domain.lower().strip().lstrip("@")
    if not first or not last or not domain:
        return []
    local = [f"{first}.{last}", f"{first[0]}{last}", f"{first}_{last}", f"{first}{last[0]}", f"{first}{last}"]
    return list(dict.fromkeys(f"{x}@{domain}" for x in local))



def extract_public_emails(text: str) -> list[str]:
    """Extract syntactically plausible public email addresses from already-fetched public text."""
    if not text:
        return []
    found = re.findall(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", text)
    out=[]; seen=set()
    for e in found:
        e=e.strip('.,;:()[]{}<>').lower()
        if e not in seen:
            seen.add(e); out.append(e)
    return out


def email_candidates(first_name: str, last_name: str, domain: str) -> list[str]:
    return [x['email'] for x in email_variants(first_name,last_name,domain)]

def valid_email_syntax(address: str) -> bool:
    _, parsed = parseaddr(address or "")
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", parsed or ""))


async def mx_records(domain: str) -> list[str]:
    resolver = dns.asyncresolver.Resolver()
    answers = await resolver.resolve(domain, "MX")
    return sorted([str(a.exchange).rstrip(".") for a in answers])


async def email_candidates_and_mx(first_name: str, last_name: str, domain: str) -> dict:
    candidates = email_variants(first_name, last_name, domain)
    try:
        mx = await mx_records(domain)
    except Exception as exc:
        mx = []
        mx_error = str(exc)
    else:
        mx_error = ""
    return {
        "candidates": [{"email": x, "syntax_ok": valid_email_syntax(x)} for x in candidates],
        "mx": mx,
        "mx_ok": bool(mx),
        "mx_error": mx_error,
    }
