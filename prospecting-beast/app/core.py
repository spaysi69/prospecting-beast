import os, re, json, asyncio, time, math, shutil, subprocess, tempfile
from urllib.parse import urlparse
from pathlib import Path
from typing import Any
import httpx
from rapidfuzz import fuzz
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from .osint_tools import xray_matrix, extract_public_emails
from .cache import CACHE

BASE = Path(__file__).resolve().parents[1]
load_dotenv(BASE / '.env')
TITLES = json.loads((BASE / 'config' / 'titles.json').read_text(encoding='utf-8'))

# Common public-company pages to probe before targeted web search.
# Keep this list conservative so Web First stays cheap and fast.
ROLE_PATHS = [
    '/leadership',
    '/executive-team',
    '/management',
    '/our-team',
    '/team',
    '/leadership-team',
    '/about/leadership',
    '/about/team',
    '/company/leadership',
    '/company/team',
    '/people',
    '/staff',
]


def title_dictionary(mode: str) -> dict[str, int]:
    mode = (mode or 'f').lower().strip()
    if mode == 'both':
        out = dict(TITLES.get('f', {}))
        for title, weight in TITLES.get('nf', {}).items():
            out[title] = max(out.get(title, 0), weight)
        return out
    if mode not in ('f', 'nf'):
        mode = 'f'
    return dict(TITLES.get(mode, {}))


def normalize_title(title: str) -> str:
    s = (title or '').lower()
    s = s.replace('&', ' and ')
    s = re.sub(r"[\u2010-\u2015\-_/|,+:;()]+", ' ', s)
    replacements = {
        r'\bit\b': 'information technology',
        r'\bcyber[ -]?security\b': 'cyber security',
        r'\binformation systems\b': 'information systems',
        r'\bsysadmin\b': 'system administrator',
        r'\bdev ops\b': 'devops',
        r'\bvp\b': 'vice president',
        r'\bcto\b': 'chief technology officer',
        r'\bcio\b': 'chief information officer',
    }
    for pat, repl in replacements.items():
        s = re.sub(pat, repl, s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def _tokens(s: str) -> set[str]:
    return set(re.findall(r'[a-z0-9]+', normalize_title(s)))


def base_score(title: str, mode: str) -> dict | None:
    title = (title or '').strip()
    if not title:
        return None
    dictionary = title_dictionary(mode)
    target_norm = normalize_title(title)
    if not target_norm:
        return None
    best = None
    title_tokens = _tokens(title)
    for target, weight in dictionary.items():
        target_normed = normalize_title(target)
        exact = 100 if target_norm == target_normed else 0
        wratio = fuzz.WRatio(target_norm, target_normed)
        token = fuzz.token_set_ratio(target_norm, target_normed)
        # Blend the fuzzy signals; WRatio handles reordered/abbreviated titles well.
        similarity = int(round(max(exact, 0.55 * wratio + 0.45 * token)))
        # Guard against very broad false positives: require either a strong fuzzy match
        # or meaningful token overlap with a high-priority title.
        overlap = len(title_tokens & _tokens(target)) / max(1, len(_tokens(target)))
        if similarity < 62 and overlap < 0.5:
            continue
        score = round(weight * (0.60 + 0.40 * similarity / 100), 1)
        if exact == 100:
            match_type = 'Exact'
        elif similarity >= 90:
            match_type = 'Very close'
        elif similarity >= 80:
            match_type = 'Close'
        elif similarity >= 70:
            match_type = 'Related'
        else:
            match_type = 'Loose'
        reason = (
            f"Closest target role: {target}. {match_type} title match ({similarity}% similarity). "
            f"Target priority: {weight}/100."
        )
        candidate = {'score': score, 'matched_title': target, 'similarity': similarity, 'base_weight': weight, 'match_type': match_type, 'match_reason': reason}
        if best is None or (candidate['score'], candidate['similarity'], candidate['base_weight']) > (best['score'], best['similarity'], best['base_weight']):
            best = candidate
    return best


def env(name: str, default: str = '') -> str:
    return os.getenv(name, default).strip()


def norm_domain(s: str) -> str:
    s = (s or '').strip()
    if not s:
        return ''
    if '://' not in s:
        s = 'https://' + s
    p = urlparse(s)
    d = (p.netloc or p.path).split(':')[0].lower().strip('.')
    if d.startswith('www.'):
        d = d[4:]
    return d


def unique_domains(values):
    return list(dict.fromkeys(d for d in (norm_domain(v) for v in values) if d))


def supabase_enabled() -> bool:
    return bool(env('SUPABASE_URL') and env('SUPABASE_SERVICE_ROLE_KEY'))


def sb_headers():
    key = env('SUPABASE_SERVICE_ROLE_KEY')
    return {
        'apikey': key,
        'Authorization': f'Bearer {key}',
        'Content-Type': 'application/json',
        'Prefer': 'return=representation',
    }


def sb_url(table: str) -> str:
    return env('SUPABASE_URL').rstrip('/') + '/rest/v1/' + table


async def sb_request(method: str, table: str, *, params=None, json_body=None, headers_extra=None):
    if not supabase_enabled():
        raise RuntimeError('Supabase is not configured. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY.')
    headers = sb_headers()
    if headers_extra:
        headers.update(headers_extra)
    timeout = float(env('REQUEST_TIMEOUT', '45'))
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as c:
        r = await c.request(method, sb_url(table), params=params, json=json_body, headers=headers)
        if r.status_code >= 400:
            raise RuntimeError(f'Supabase {method} {table} failed: {r.status_code} {r.text[:500]}')
        if not r.content:
            return []
        return r.json()


async def sb_select(table: str, *, params=None):
    return await sb_request('GET', table, params=params)


async def sb_insert(table: str, row: dict):
    out = await sb_request('POST', table, json_body=row)
    return out[0] if isinstance(out, list) and out else None


async def sb_upsert(table: str, row: dict, on_conflict: str):
    params = {'on_conflict': on_conflict}
    headers = {'Prefer': 'resolution=merge-duplicates,return=representation'}
    out = await sb_request('POST', table, params=params, json_body=row, headers_extra=headers)
    return out[0] if isinstance(out, list) and out else None


async def sb_update(table: str, filters: dict, patch: dict):
    params = {k: f'eq.{v}' for k, v in filters.items()}
    return await sb_request('PATCH', table, params=params, json_body=patch)


async def sb_delete(table: str, filters: dict):
    params = {k: f'eq.{v}' for k, v in filters.items()}
    return await sb_request('DELETE', table, params=params)


async def http_client():
    return httpx.AsyncClient(timeout=float(env('REQUEST_TIMEOUT', '45')), follow_redirects=True)



async def crawl_public_company_pages(domain: str, limit: int = 8) -> list[dict]:
    """Free-first discovery from public company pages."""
    pages=[]
    headers={'User-Agent':'Mozilla/5.0 (compatible; ProspectingBeast/1.0)'}
    async with await http_client() as c:
        for path in ROLE_PATHS[:limit]:
            try:
                r=await c.get(f'https://{domain}{path}',headers=headers)
                if r.status_code >= 400 or 'text/html' not in (r.headers.get('content-type') or ''): continue
                soup=BeautifulSoup(r.text,'html.parser')
                for tag in soup(['script','style','noscript','svg']): tag.decompose()
                text=' '.join(soup.stripped_strings)
                if text: pages.append({'url':str(r.url),'title':soup.title.get_text(' ',strip=True) if soup.title else '', 'content':text[:12000]})
            except Exception: continue
    return pages

async def gemini_extract_people(company: str, domain: str, evidence: list[dict], mode: str) -> list[dict]:
    key=env('GEMINI_API_KEY'); model=env('GEMINI_MODEL','gemini-3.5-flash-lite')
    if not key: return []
    targets=list(title_dictionary(mode).keys())
    prompt=('Extract only publicly presented current employees/leaders from supplied web evidence. Do not invent people. '
            'Prefer full name, current job title, LinkedIn URL when explicitly present, and source URL. '
            'Include titles close/related to target roles; local fuzzy matching decides qualification. '
            'Ignore customers, competitors, authors, speakers, and unclear employment. '
            'Return ONLY JSON: {"people":[{"name":"","title":"","linkedin_url":"","source_url":""}]}')
    payload={'company':company,'domain':domain,'mode':mode,'targets':targets,'evidence':evidence}
    cached=CACHE.get('gemini_people', payload)
    if cached is not None:
        return cached
    body={'contents':[{'parts':[{'text':prompt+f'\nTarget company: {company} ({domain})\nTarget roles: {json.dumps(targets)}\nEvidence:\n{json.dumps(evidence)[:50000]}'}]}], 'generationConfig':{'responseMimeType':'application/json','temperature':0.0}}
    async with await http_client() as c:
        try:
            r=await c.post(f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}',json=body); r.raise_for_status()
            obj=json.loads(r.json()['candidates'][0]['content']['parts'][0]['text']); result=obj.get('people',[]) if isinstance(obj,dict) else []
            CACHE.set('gemini_people', payload, result)
            return result
        except Exception: return []


ROLE_FAMILIES = {
    'it_leadership': ['IT Manager','IT Director','Head of IT','VP of IT','CIO','CTO'],
    'systems_network': ['System Administrator','Systems Administrator','Network Manager','Network Administrator','Infrastructure Manager','IT Infrastructure Manager','Head of Infrastructure'],
    'security': ['Security Manager','IT Security Manager','Cyber Security Manager','Cybersecurity Manager','Information Security Manager','Head of Information Security'],
    'cloud_devops_data': ['IT Cloud Manager','Cloud Manager','Cloud Infrastructure Manager','Head of DevOps','DevOps Manager','Head of Data','Transformation Manager'],
    'operations_support': ['IT Operations Manager','Operations Manager','End User Manager','Help Desk Manager','IT Support Manager','Service Desk Manager','IT Admin'],
}

async def spiderfoot_osint(domain: str) -> list[dict]:
    """Optional passive SpiderFoot connector.

    It is intentionally opt-in. Configure SPIDERFOOT_URL to a SpiderFoot REST
    server you control. No public/private credential scraping is performed.
    """
    base = env('SPIDERFOOT_URL')
    if not base:
        return []
    target = norm_domain(domain)
    if not target:
        return []
    try:
        async with await http_client() as c:
            # Support the common SpiderFoot REST shape documented by the project.
            start = await c.post(base.rstrip('/') + '/startscan', data={
                'scanname': f'ProspectingBeast-{target}',
                'scantarget': target,
                'typelist': 'passive',
            })
            if start.status_code >= 400:
                return []
            payload = start.json() if start.content else {}
            sid = payload.get('id') or payload.get('scanid') or payload.get('scanId')
            if not sid:
                return []
            # Bounded polling. Passive OSINT should be short-lived for prospecting.
            for _ in range(20):
                await asyncio.sleep(2)
                st = await c.post(base.rstrip('/') + '/scanstatus', data={'id': sid})
                if st.status_code < 400:
                    obj = st.json() if st.content else {}
                    status = str(obj.get('status') or '').upper()
                    if status in {'FINISHED','ERROR-FAILED','ABORTED'}:
                        break
            res = await c.post(base.rstrip('/') + '/scaneventresultsunique', data={'id': sid, 'eventType': 'ALL'})
            if res.status_code >= 400 or not res.content:
                return []
            arr = res.json()
            if isinstance(arr, dict):
                arr = arr.get('results') or arr.get('data') or []
            out=[]
            for row in arr or []:
                data = row.get('data') if isinstance(row,dict) else None
                if isinstance(data, str) and ('@' not in data):
                    continue
                out.append({'source':'spiderfoot','title': row.get('type','SpiderFoot'), 'url':'', 'content':str(data or row)[:6000], 'score':0.5})
            return out
    except Exception:
        return []

async def amass_passive_osint(domain: str) -> list[dict]:
    """Optional local Amass passive enumeration for domain clues only."""
    if str(env('AMASS_ENABLED','false')).lower() not in {'1','true','yes','on'}:
        return []
    binary = env('AMASS_BIN') or shutil.which('amass')
    if not binary:
        return []
    target = norm_domain(domain)
    try:
        proc = await asyncio.create_subprocess_exec(binary, 'enum', '--passive', '-d', target, '-silent', stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=float(env('AMASS_TIMEOUT','45')))
        rows=[]
        for line in stdout.decode(errors='ignore').splitlines():
            host=line.strip()
            if host and host.endswith('.'+target):
                rows.append({'source':'amass','title':'Amass passive domain discovery','url':'https://'+host,'content':host,'score':0.4})
        return rows[:100]
    except Exception:
        return []


def build_role_queries(qbase: str, domain: str, mode: str, location: str = "", industry: str = "") -> list[tuple[str,str,list[str]|None]]:
    """Build a diverse X-ray/search matrix from the configured target roles.

    The matrix broadens recall without attempting to bypass provider pagination or
    anti-bot controls. Search results are post-processed locally.
    """
    rows = xray_matrix(qbase, [domain] if domain else [], list(title_dictionary(mode).keys()), location, industry)
    out=[]
    for row in rows:
        q=row.get('query','')
        family=row.get('family','targeted')
        kind=row.get('engine','web')
        domains=['linkedin.com'] if kind=='linkedin' else ([domain] if kind=='official' and domain else None)
        out.append((q, kind, domains))
    return out



async def spiderfoot_osint(domain: str) -> list[dict]:
    """Optional passive SpiderFoot connector. Requires SPIDERFOOT_URL pointing to a server you control."""
    base = env('SPIDERFOOT_URL')
    if not base or str(env('SPIDERFOOT_ENABLED','false')).lower() not in {'1','true','yes','on'}:
        return []
    target = norm_domain(domain)
    try:
        async with await http_client() as c:
            start = await c.post(base.rstrip('/') + '/startscan', data={
                'scanname': f'ProspectingBeast-{target}',
                'scantarget': target,
                'typelist': 'passive',
            })
            if start.status_code >= 400:
                return []
            obj = start.json() if start.content else {}
            sid = obj.get('id') or obj.get('scanid') or obj.get('scanId')
            if not sid:
                return []
            for _ in range(int(env('SPIDERFOOT_POLL_STEPS','15'))):
                await asyncio.sleep(float(env('SPIDERFOOT_POLL_SECONDS','2')))
                st = await c.post(base.rstrip('/') + '/scanopts', data={'id': sid})
                if st.status_code >= 400:
                    break
            res = await c.post(base.rstrip('/') + '/scaneventresultsunique', data={'id': sid, 'eventType': 'ALL'})
            if res.status_code >= 400 or not res.content:
                return []
            arr = res.json()
            if isinstance(arr, dict): arr = arr.get('results') or arr.get('data') or []
            out=[]
            for row in arr or []:
                if not isinstance(row, dict): continue
                text=str(row.get('data') or '')
                out.append({'source':'spiderfoot','title':row.get('type') or 'SpiderFoot','url':'','content':text[:6000],'score':0.4})
            return out
    except Exception:
        return []

async def amass_passive_osint(domain: str) -> list[dict]:
    """Optional local passive Amass domain mapping. Disabled by default."""
    if str(env('AMASS_ENABLED','false')).lower() not in {'1','true','yes','on'}:
        return []
    binary = env('AMASS_BIN') or shutil.which('amass')
    if not binary:
        return []
    target = norm_domain(domain)
    try:
        proc = await asyncio.create_subprocess_exec(binary,'enum','--passive','-d',target,'-silent',stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        stdout,_=await asyncio.wait_for(proc.communicate(),timeout=float(env('AMASS_TIMEOUT','45')))
        return [{'source':'amass','title':'Amass passive hostname','url':'https://'+h,'content':h,'score':0.35} for h in stdout.decode(errors='ignore').splitlines() if h.strip().endswith('.'+target)][:100]
    except Exception:
        return []


async def gemini_plan_search_queries(company: str, domain: str, mode: str, location: str = "", industry: str = "", existing_titles: list[str] | None = None, max_queries: int = 6) -> list[str]:
    """Optional agentic planner: uses Gemini to choose the next public-web queries.

    It is deliberately constrained to public search queries and cannot browse LinkedIn directly.
    If Gemini is unavailable or produces invalid output, return an empty list and the caller uses
    the deterministic query matrix.
    """
    key=env('GEMINI_API_KEY')
    if not key or str(env('AGENTIC_DISCOVERY','false')).lower() not in {'1','true','yes','on'}:
        return []
    model=env('GEMINI_MODEL','gemini-3.5-flash-lite')
    targets=list(title_dictionary(mode).keys())
    prompt=(
        'You are an OSINT search planner for B2B prospect discovery. Return only a JSON object with a list of search queries. '
        'Queries must use only public web search syntax (no login, no scraping instructions, no evasion). '
        'Prefer targeted LinkedIn-indexed and official-company searches. Do not invent a company domain. '
        f'Target company: {company} ({domain}). Target roles: {json.dumps(targets)}. '
        f'Location: {location}. Industry: {industry}. Existing discovered titles: {json.dumps(existing_titles or [])}. '
        f'Generate at most {max_queries} high-signal queries that are materially different.'
    )
    body={'contents':[{'parts':[{'text':prompt}]}], 'generationConfig':{'responseMimeType':'application/json','temperature':0.0}}
    try:
        async with await http_client() as c:
            r=await c.post(f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}',json=body)
            r.raise_for_status(); data=r.json()
            txt=next((p.get('text') for p in ((data.get('candidates') or [{}])[0].get('content') or {}).get('parts') or [] if isinstance(p,dict) and p.get('text')), '')
            obj=json.loads(txt) if txt else {}
            queries=obj.get('queries',[]) if isinstance(obj,dict) else []
            return [q.strip() for q in queries if isinstance(q,str) and q.strip()][:max_queries]
    except Exception:
        return []


def adaptive_search_budget(domain: str, requested: int) -> tuple[int, str]:
    requested=max(1,min(int(requested or 8),16))
    mega={"microsoft.com","google.com","apple.com","amazon.com","meta.com","ibm.com","oracle.com","salesforce.com","sap.com","adobe.com","intel.com","nvidia.com","cisco.com","accenture.com","deloitte.com","pwc.com","ey.com","kpmg.com"}
    small_hint={x.strip().lower() for x in env('PB_STARTUP_DOMAINS','').split(',') if x.strip()}
    d=norm_domain(domain)
    if d in mega: return min(requested,4), 'mega-corp'
    if d in small_hint: return max(requested,12), 'startup-hint'
    return requested, 'default'

async def web_people_discovery(company: str, domain: str, mode: str, max_people: int=10, search_budget: int=8, location: str="", industry: str="", agentic: bool=False) -> dict:
    """Web-first employee discovery.

    Strategy:
      1) Crawl likely official team/leadership pages.
      2) Run targeted LinkedIn-indexed searches by role family.
      3) Run official-domain and general-web searches to catch people missed by LinkedIn indexing.
      4) Re-extract candidates in small batches, dedupe aggressively, and stop once enough strong matches exist.

    No paid employee database is required. Tavily is the public-web retrieval layer; Gemini only structures evidence.
    """
    max_people = max(1, min(int(max_people or 10), 25))
    search_budget, budget_reason = adaptive_search_budget(domain, search_budget)
    qbase = (company or '').strip() or domain

    evidence: list[dict] = []
    people: list[dict] = []

    # Pass 1: likely first-party team pages.
    official_pages = await crawl_public_company_pages(domain, 8)
    for pg in official_pages:
        evidence.append({
            'title': pg.get('title',''), 'url': pg.get('url',''),
            'content': pg.get('content','')[:12000], 'score': 1.0, 'query': 'official_company_page'
        })
    if evidence:
        people.extend(await gemini_extract_people(qbase, domain, evidence, mode))
        # Absolute quota guard after the first extraction batch. Do not run Tavily or
        # optional passive connectors when the requested number is already satisfied.
        initial_qualified=sum(1 for p in people if base_score(p.get('title') or p.get('job_title') or '', mode))
        if initial_qualified >= max_people:
            return {
                'people': people[:max_people], 'evidence_count': len(evidence), 'query_count': 0,
                'estimated_tavily_credits': 0, 'target_terms': list(title_dictionary(mode).keys()),
                'sources_used': ['official_company_page'], 'budget': search_budget, 'budget_reason': budget_reason,
            }

    # Adaptive OSINT: official pages + optional passive open-source connectors + targeted public-web queries.
    official_pages = official_pages  # keep first-party evidence gathered above
    evidence.extend(await spiderfoot_osint(domain))
    evidence.extend(await amass_passive_osint(domain))
    queries = build_role_queries(qbase, domain, mode, location, industry)[:search_budget]

    # Keep a compact evidence set to prevent prompts from becoming huge.
    seen_urls={e.get('url') for e in evidence if e.get('url')}
    extracted_keys=set()
    def add_people(rows):
        for p in rows or []:
            name=(p.get('name') or p.get('full_name') or '').strip()
            title=(p.get('title') or p.get('job_title') or '').strip()
            li=(p.get('linkedin_url') or '').strip()
            if not name or not title:
                continue
            k=(li.lower() or name.lower(), normalize_title(title))
            if k not in extracted_keys:
                extracted_keys.add(k)
                people.append(p)

    add_people(people)

    # Search in small rounds. This means a good company can stop early instead of
    # spending all 8–12 searches every time.
    for i, item in enumerate(queries):
        q, qkind, qdomains = item
        is_linkedin = qkind == 'linkedin'
        depth = 'advanced' if (is_linkedin and i < 2) else 'basic'
        include_domains = qdomains if qdomains else None
        exclude_domains = ['facebook.com','instagram.com','x.com','twitter.com','youtube.com'] if is_linkedin else None
        try:
            d = await tavily_search(
                q,
                max_results=6,
                search_depth=depth,
                include_domains=include_domains,
                exclude_domains=exclude_domains,
                include_raw_content=False,
            )
        except Exception as exc:
            # Keep the discovery run alive if a single search fails.
            continue

        for x in d.get('results', []):
            u=(x.get('url') or '').strip()
            if not u or u in seen_urls:
                continue
            score=float(x.get('score') or 0)
            if score and score < 0.15:
                continue
            seen_urls.add(u)
            evidence.append({
                'title':x.get('title',''), 'url':u,
                'content':(x.get('content') or '')[:6000],
                'score':score, 'query':q,
            })

        # Re-extract after every 2 searches and stop once enough quality prospects exist.
        if (i + 1) % 2 == 0 or i == len(queries)-1:
            batch = await gemini_extract_people(qbase, domain, evidence[-32:], mode)
            add_people(batch)
            # Hard stop: once a Gemini extraction batch yields enough qualified people,
            # do not spend another Tavily query or another extraction round.
            qualified=sum(1 for p in people if base_score(p.get('title') or p.get('job_title') or '', mode))
            if qualified >= max_people:
                break

    # Final extraction from the best evidence if needed.
    if not people and evidence:
        add_people(await gemini_extract_people(qbase, domain, evidence[-48:], mode))

    # Local qualification, dedupe, and ranking happen in the caller as well; doing a
    # first pass here keeps the returned set compact and prevents irrelevant web hits
    # from being persisted.
    qualified=[]; seen=set()
    for p in people:
        name=(p.get('name') or p.get('full_name') or '').strip()
        title=(p.get('title') or p.get('job_title') or '').strip()
        li=(p.get('linkedin_url') or '').strip()
        if not name or not title:
            continue
        info=base_score(title, mode)
        if not info:
            continue
        k=(li.lower() or name.lower(), normalize_title(title), domain)
        if k in seen:
            continue
        seen.add(k)
        p['name']=name; p['title']=title; p['linkedin_url']=li
        p['_source']='public_web'
        p['_source_url']=p.get('source_url') or ''
        source_url=p.get('source_url') or ''
        p['_search_score']=max([float(x.get('score') or 0) for x in evidence if x.get('url')==source_url] or [0])
        qualified.append(p)

    # Sort before truncating so the caller sees the most relevant prospects first.
    qualified.sort(key=lambda p: (
        (base_score(p.get('title',''), mode) or {}).get('score',0),
        (base_score(p.get('title',''), mode) or {}).get('similarity',0),
        p.get('_search_score',0)
    ), reverse=True)

    return {
        'people': qualified[:max_people],
        'evidence_count': len(evidence),
        'query_count': len(queries),
        'estimated_tavily_credits': sum(2 if (item[1]=='linkedin' and i < 2) else 1 for i, item in enumerate(queries)),
        'target_terms': list(title_dictionary(mode).keys()),
        'sources_used': sorted(set((e.get('source') or e.get('query') or 'web').split(':')[0] for e in evidence)),
        'budget': search_budget, 'budget_reason': budget_reason,
    }

async def tavily_search(query: str, max_results: int = 6, *, search_depth: str='basic', include_domains=None, exclude_domains=None, include_raw_content: bool=False):
    key = env('TAVILY_API_KEY')
    if not key:
        raise RuntimeError('Missing TAVILY_API_KEY')
    payload={
        'query': query,
        'search_depth': search_depth,
        'topic': 'general',
        'max_results': max(1, min(int(max_results), 10)),
        'include_answer': False,
        'include_raw_content': include_raw_content,
    }
    if include_domains:
        payload['include_domains']=include_domains
    if exclude_domains:
        payload['exclude_domains']=exclude_domains
    cached = CACHE.get('tavily', payload)
    if cached is not None:
        return cached
    async with await http_client() as c:
        r = await c.post('https://api.tavily.com/search',
                         headers={'Authorization': f'Bearer {key}', 'Content-Type':'application/json'},
                         json=payload)
        r.raise_for_status()
        data = r.json()
        CACHE.set('tavily', payload, data)
        return data


async def gemini_classify(company: str, domain: str, evidence: list[dict]):
    key = env('GEMINI_API_KEY')
    model = env('GEMINI_MODEL', 'gemini-3.5-flash-lite')
    if not key:
        raise RuntimeError('Missing GEMINI_API_KEY')
    prompt = """You are a strict corporate-structure verifier. Use ONLY the supplied search evidence. Identify credible corporate-family entities directly related to the target company. Valid relationships: parent, subsidiary, sister, brand, acquired_target, related, unknown. Do not invent domains. Prefer official company pages, filings, exchange disclosures, acquisition announcements, or reputable business sources. Return ONLY JSON in this shape: {\"relationships\":[{\"company\":\"...\",\"domain\":\"...\",\"relationship\":\"parent|subsidiary|sister|brand|acquired_target|related|unknown\",\"confidence\":0.0,\"reason\":\"...\"}]}. Ignore entities that are merely customers, partners, suppliers, competitors, people, or articles that mention the company without a corporate relationship."""
    payload_evidence = evidence or []
    cache_payload={'company':company,'domain':domain,'evidence':payload_evidence}
    cached=CACHE.get('gemini_family', cache_payload)
    if cached is not None:
        return cached
    body = {'contents':[{'parts':[{'text': prompt + f'\\nTarget: {company} ({domain})\\nEvidence:\\n{json.dumps(payload_evidence)[:45000]}'}]}], 'generationConfig':{'responseMimeType':'application/json','temperature':0.0}}
    try:
        async with await http_client() as c:
            r = await c.post(f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}', json=body)
            r.raise_for_status()
            data = r.json()
            candidates = data.get('candidates') or []
            if not candidates:
                return []
            parts = ((candidates[0].get('content') or {}).get('parts') or [])
            text = next((p.get('text') for p in parts if isinstance(p, dict) and p.get('text')), '')
            if not text:
                return []
            obj = json.loads(text)
            rels = obj.get('relationships', []) if isinstance(obj, dict) else []
            result = rels if isinstance(rels, list) else []
            CACHE.set('gemini_family', cache_payload, result)
            return result
    except Exception:
        return []

async def discover_family(company_name: str, domain: str):
    """Return (relationships, tavily_estimated_credits). Always return a 2-tuple."""
    qbase = company_name if company_name and company_name != domain else domain
    queries = [
        f'"{qbase}" parent company subsidiary',
        f'"{qbase}" acquired by acquired subsidiary',
        f'"{qbase}" "part of" group holding company',
        f'"{qbase}" owns owned by subsidiary',
    ]
    allres=[]
    estimated_credits=0
    for i, q in enumerate(queries):
        try:
            depth = 'advanced' if i < 2 else 'basic'
            d=await tavily_search(q, max_results=6, search_depth=depth, include_raw_content=False)
            estimated_credits += 2 if depth == 'advanced' else 1
            allres.extend(d.get('results',[]) or [])
        except Exception:
            continue
    seen=set(); evidence=[]
    for x in sorted(allres, key=lambda r: float(r.get('score') or 0), reverse=True):
        u=x.get('url','')
        if u and u not in seen:
            seen.add(u)
            evidence.append({'title':x.get('title',''),'url':u,'content':(x.get('content') or '')[:4000],'score':float(x.get('score') or 0)})
    if not evidence:
        return [], estimated_credits
    raw=await gemini_classify(qbase, domain, evidence)
    cleaned=[]; seen_domains=set()
    for rr in raw or []:
        if not isinstance(rr, dict):
            continue
        rd=norm_domain(rr.get('domain',''))
        rel=(rr.get('relationship') or 'unknown').lower().strip()
        try:
            conf=float(rr.get('confidence') or 0)
        except (TypeError, ValueError):
            conf=0.0
        if not rd or rd==domain or rd in seen_domains:
            continue
        if rel not in RELATIONSHIP_QUEUE_PRIORITY:
            rel='related'
        item=dict(rr)
        item['domain']=rd
        item['relationship']=rel
        item['confidence']=conf
        item['queue_score']=relationship_queue_score(item)
        cleaned.append(item)
        seen_domains.add(rd)
    cleaned.sort(key=lambda x:(x.get('queue_score',0), float(x.get('confidence',0) or 0)), reverse=True)
    return cleaned, estimated_credits


class SeamlessPool:
    def __init__(self):
        raw = env('SEAMLESS_API_KEYS')
        if not raw:
            raw = env('SEAMLESS_API_KEY')
        pool = [k.strip() for part in re.split(r'[,;\n]+', raw) for k in [part.strip()] if k]
        # Also support SEAMLESS_API_KEY_1, _2, _3, ... with no hard-coded maximum.
        for name, value in sorted(os.environ.items()):
            if re.fullmatch(r'SEAMLESS_API_KEY_\d+', name) and value.strip():
                pool.append(value.strip())
        self.keys = list(dict.fromkeys(pool))
        self.bad = set()
        self.base = env('SEAMLESS_BASE_URL','https://api.seamless.ai/api/client/v1').rstrip('/')
        self.timeout = float(env('REQUEST_TIMEOUT','45'))
        self.lock = asyncio.Lock()
        self.cursor = 0

    def available_indices(self):
        return [i for i,k in enumerate(self.keys) if i not in self.bad]

    async def research(self, person: dict):
        if not self.keys:
            raise RuntimeError('No Seamless API keys configured. Add SEAMLESS_API_KEYS=key1,key2,key3')
        payload={'contacts':[{'contactName':person.get('name',''),'domain':person.get('domain',''),'title':person.get('title',''),'liProfileUrl':person.get('linkedin_url','')}]}
        async with self.lock:
            indices = self.available_indices()
            if not indices:
                raise RuntimeError('All Seamless API keys are unavailable for this run.')
            # round-robin, skipping failed keys
            order = indices[self.cursor % len(indices):] + indices[:self.cursor % len(indices)]
            self.cursor += 1
        last_err=None
        for idx in order:
            key=self.keys[idx]
            try:
                async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as c:
                    r=await c.post(self.base+'/contacts/research',headers={'Token':key,'Authorization':f'Bearer {key}','Content-Type':'application/json','accept':'application/json'},json=payload)
                    if r.status_code in (401,403,429,402,422):
                        try:
                            errj=r.json()
                        except Exception:
                            errj={}
                        msg=json.dumps(errj).lower()
                        # 422 can mean insufficient credits or malformed input. Only rotate it when the response points to credits.
                        if r.status_code != 422 or 'credit' in msg or 'additionalcreditsneeded' in msg:
                            self.bad.add(idx); last_err=f'Seamless key {idx+1} unavailable: HTTP {r.status_code}'; continue
                        r.raise_for_status()
                    r.raise_for_status()
                    reqs=r.json().get('requestIds',[])
                    if not reqs:
                        last_err=f'Seamless key {idx+1} returned no request IDs'; continue
                    for _ in range(20):
                        await asyncio.sleep(2)
                        p=await c.get(self.base+'/contacts/research/poll',headers={'Token':key,'accept':'application/json'},params={'requestIds':','.join(reqs)})
                        if p.status_code in (401,403,429,402):
                            self.bad.add(idx); last_err=f'Seamless key {idx+1} poll failed: HTTP {p.status_code}'; break
                        p.raise_for_status()
                        d=p.json().get('data',[])
                        if d and all(x.get('status') in ('done','failed') for x in d):
                            result=d[0].get('contact',d[0])
                            result['_seamless_key_index']=idx+1
                            return result
                    else:
                        last_err=f'Seamless key {idx+1} timed out'; continue
            except Exception as e:
                last_err=str(e); continue
        raise RuntimeError(last_err or 'Seamless enrichment failed')
