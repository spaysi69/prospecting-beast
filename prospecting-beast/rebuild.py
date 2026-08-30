from pathlib import Path
import re, textwrap
root=Path('/mnt/data/pb_v33_audit')

# ---- core.py targeted patches ----
p=root/'app/core.py'
s=p.read_text()

# import cache helpers
s=s.replace('from .osint_tools import xray_matrix, extract_public_emails\n', 'from .osint_tools import xray_matrix, extract_public_emails\nfrom .cache import cache_get_json, cache_set_json, cache_key_for\n')

# Add adaptive budget helper before web_people_discovery
marker='async def web_people_discovery(company: str, domain: str, mode: str, max_people: int=10, search_budget: int=8, location: str="", industry: str="", agentic: bool=False) -> dict:\n'
helper='''def _adaptive_search_budget(domain: str, requested: int, official_pages: list[dict]) -> int:\n    """Keep search spend proportional to likely company size without making paid calls.\n\n    Mega-corp domains are aggressively capped because they already have rich public indexing.\n    For smaller organizations, a public employee-range signal on official pages can unlock a larger budget.\n    """\n    requested=max(1, min(int(requested or 8), 16))\n    d=norm_domain(domain)\n    mega={\n        'microsoft.com','google.com','alphabet.com','amazon.com','apple.com','meta.com',\n        'oracle.com','ibm.com','salesforce.com','nvidia.com','walmart.com','adobe.com'\n    }\n    if d in mega:\n        return min(requested, 4)\n    text=' '.join((x.get('content') or '')[:7000] for x in (official_pages or []))\n    m=re.search(r'(?i)(?:employees|team size|company size)[^\\d]{0,35}([\\d,]+)\\s*(?:employees)?', text)\n    if m:\n        try:\n            n=int(m.group(1).replace(',',''))\n            if n < 500:\n                return min(max(requested, 12), 16)\n            if n >= 5000:\n                return min(requested, 4)\n        except ValueError:\n            pass\n    return requested\n\n\n'''
if marker in s and '_adaptive_search_budget' not in s:
    s=s.replace(marker, helper+marker)

# Replace the discovery function wholesale.
start=s.index('async def web_people_discovery(')
end=s.index('\nasync def tavily_search(', start)
new_func=r'''async def web_people_discovery(company: str, domain: str, mode: str, max_people: int=10, search_budget: int=8, location: str="", industry: str="", agentic: bool=False) -> dict:
    """Web-first employee discovery with a hard candidate quota.

    The quota is enforced before any additional Tavily search. Every Gemini extraction
    batch is followed immediately by a qualified-count check, so a company that already
    has enough qualified people never burns more search budget.
    """
    max_people=max(1, min(int(max_people or 10), 25))
    search_budget=max(1, min(int(search_budget or 8), 16))
    qbase=(company or '').strip() or domain
    evidence:list[dict]=[]
    people:list[dict]=[]
    official_pages=await crawl_public_company_pages(domain, 8)
    for pg in official_pages:
        evidence.append({'title':pg.get('title',''),'url':pg.get('url',''),'content':pg.get('content','')[:12000],'score':1.0,'query':'official_company_page'})

    seen_urls={e.get('url') for e in evidence if e.get('url')}
    extracted_keys=set()
    def add_people(rows):
        for p in rows or []:
            name=(p.get('name') or p.get('full_name') or '').strip()
            title=(p.get('title') or p.get('job_title') or '').strip()
            li=(p.get('linkedin_url') or '').strip()
            if not name or not title:
                continue
            k=(li.lower() or name.lower(), normalize_title(title), domain)
            if k in extracted_keys:
                continue
            extracted_keys.add(k); people.append(p)

    def qualified_count(rows):
        return sum(1 for p in rows if base_score(p.get('title') or p.get('job_title') or '', mode))

    # Gemini batch #1: official/first-party evidence.
    if evidence:
        batch=await gemini_extract_people(qbase, domain, evidence, mode)
        add_people(batch)
        # Absolute quota check immediately after the extraction batch.
        if qualified_count(people) >= max_people:
            qualified=[p for p in people if base_score(p.get('title') or p.get('job_title') or '', mode)]
            return _finalize_people_result(qualified, people, evidence, [], max_people, mode)

    # Optional passive sources are fetched before Tavily. They are not Tavily searches.
    evidence.extend(await spiderfoot_osint(domain))
    evidence.extend(await amass_passive_osint(domain))
    if qualified_count(people) >= max_people:
        qualified=[p for p in people if base_score(p.get('title') or p.get('job_title') or '', mode)]
        return _finalize_people_result(qualified, people, evidence, [], max_people, mode)

    search_budget=_adaptive_search_budget(domain, search_budget, official_pages)
    if agentic:
        planned=await gemini_plan_search_queries(qbase, domain, mode, location, industry, [p.get('title') for p in people], max_queries=search_budget)
        queries=[(q,'agentic',None) for q in planned] or build_role_queries(qbase, domain, mode, location, industry)[:search_budget]
    else:
        queries=build_role_queries(qbase, domain, mode, location, industry)[:search_budget]

    used_queries=[]
    for i,(q,qkind,qdomains) in enumerate(queries):
        # Never perform another Tavily call after reaching the company's qualification quota.
        if qualified_count(people) >= max_people:
            break
        depth='advanced' if (qkind=='linkedin' and i < 2) else 'basic'
        include_domains=qdomains if qdomains else None
        exclude_domains=['facebook.com','instagram.com','x.com','twitter.com','youtube.com'] if qkind=='linkedin' else None
        try:
            d=await tavily_search(q,max_results=6,search_depth=depth,include_domains=include_domains,exclude_domains=exclude_domains,include_raw_content=False)
        except Exception:
            continue
        used_queries.append((q,qkind,qdomains))
        for x in d.get('results',[]) or []:
            u=(x.get('url') or '').strip()
            if not u or u in seen_urls:
                continue
            score=float(x.get('score') or 0)
            if score and score < 0.15:
                continue
            seen_urls.add(u)
            evidence.append({'title':x.get('title',''),'url':u,'content':(x.get('content') or '')[:6000],'score':score,'query':q})

        # Gemini extraction is deliberately per search batch so the hard quota check
        # happens immediately rather than every two searches.
        batch=await gemini_extract_people(qbase, domain, evidence[-32:], mode)
        add_people(batch)
        if qualified_count(people) >= max_people:
            break

    qualified=[p for p in people if base_score(p.get('title') or p.get('job_title') or '', mode)]
    return _finalize_people_result(qualified, people, evidence, used_queries, max_people, mode)


def _finalize_people_result(qualified, people, evidence, queries, max_people, mode):
    seen=set(); cleaned=[]
    for p in qualified or []:
        name=(p.get('name') or p.get('full_name') or '').strip()
        title=(p.get('title') or p.get('job_title') or '').strip()
        li=(p.get('linkedin_url') or '').strip()
        if not name or not title:
            continue
        info=base_score(title, mode)
        if not info:
            continue
        k=(li.lower() or name.lower(), normalize_title(title), p.get('domain') or '')
        if k in seen: continue
        seen.add(k)
        p['name']=name; p['title']=title; p['linkedin_url']=li
        p['_source']='public_web'; p['_source_url']=p.get('source_url') or ''
        src=p.get('source_url') or ''
        p['_search_score']=max([float(x.get('score') or 0) for x in evidence if x.get('url')==src] or [0])
        cleaned.append(p)
    cleaned.sort(key=lambda p:(
        (base_score(p.get('title',''), mode) or {}).get('score',0),
        (base_score(p.get('title',''), mode) or {}).get('similarity',0),
        p.get('_search_score',0)
    ), reverse=True)
    return {
        'people':cleaned[:max_people],
        'evidence_count':len(evidence),
        'query_count':len(queries),
        'estimated_tavily_credits':sum(2 if (item[1]=='linkedin' and i<2) else 1 for i,item in enumerate(queries)),
        'target_terms':list(title_dictionary(mode).keys()),
        'sources_used':sorted(set((e.get('source') or e.get('query') or 'web').split(':')[0] for e in evidence)),
    }
'''
s=s[:start]+new_func+s[end:]

# Cache Tavily calls by payload.
old="""async def tavily_search(query: str, max_results: int = 6, *, search_depth: str='basic', include_domains=None, exclude_domains=None, include_raw_content: bool=False):
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
    async with await http_client() as c:
        r = await c.post('https://api.tavily.com/search',
                         headers={'Authorization': f'Bearer {key}', 'Content-Type':'application/json'},
                         json=payload)
        r.raise_for_status()
        return r.json()
"""
new="""async def tavily_search(query: str, max_results: int = 6, *, search_depth: str='basic', include_domains=None, exclude_domains=None, include_raw_content: bool=False):
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
    cache_key=cache_key_for('tavily', payload)
    cached=await cache_get_json(cache_key)
    if cached is not None:
        return cached
    async with await http_client() as c:
        r = await c.post('https://api.tavily.com/search',
                         headers={'Authorization': f'Bearer {key}', 'Content-Type':'application/json'},
                         json=payload)
        r.raise_for_status()
        data=r.json()
    await cache_set_json(cache_key, data)
    return data
"""
if old not in s: raise SystemExit('tavily block not found')
s=s.replace(old,new)

# Cache Gemini people extraction: wrap HTTP call after prompt created.
needle="    body={'contents':[{'parts':[{'text':prompt}]}], 'generationConfig':{'responseMimeType':'application/json','temperature':0.0}}\n    try:\n"
replacement="    body={'contents':[{'parts':[{'text':prompt}]}], 'generationConfig':{'responseMimeType':'application/json','temperature':0.0}}\n    cache_key=cache_key_for('gemini_extract_people', {'company':company,'domain':domain,'mode':mode,'evidence':evidence})\n    cached=await cache_get_json(cache_key)\n    if isinstance(cached,list):\n        return cached\n    try:\n"
if needle not in s: raise SystemExit('gemini people block not found')
s=s.replace(needle,replacement,1)
needle2="            obj=json.loads(txt) if txt else {}\n            return obj.get('people',[]) if isinstance(obj,dict) else []\n    except Exception:\n        return []\n"
replacement2="            obj=json.loads(txt) if txt else {}\n            people=obj.get('people',[]) if isinstance(obj,dict) else []\n            await cache_set_json(cache_key, people)\n            return people\n    except Exception:\n        return []\n"
if needle2 in s:
    s=s.replace(needle2,replacement2,1)
else:
    print('gemini return block not exact; skipped cache write')

p.write_text(s)

# ---- cache.py ----
(root/'app/cache.py').write_text(textwrap.dedent(r'''
    import asyncio, hashlib, json, os, sqlite3, time
    from pathlib import Path

    BASE=Path(__file__).resolve().parents[1]
    DB_PATH=Path(os.getenv('PB_CACHE_DB', str(BASE/'.cache'/'osint.sqlite3')))
    TTL_SECONDS=7*24*60*60

    def cache_key_for(namespace: str, payload) -> str:
        raw=json.dumps(payload, sort_keys=True, separators=(',',':'), ensure_ascii=False, default=str)
        return namespace+':'+hashlib.sha256(raw.encode('utf-8')).hexdigest()

    def _init():
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(DB_PATH) as con:
            con.execute('CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT NOT NULL, created_at REAL NOT NULL)')
            con.execute('CREATE INDEX IF NOT EXISTS idx_cache_created ON cache(created_at)')
            con.commit()

    def _get(key):
        _init()
        now=time.time()
        with sqlite3.connect(DB_PATH) as con:
            row=con.execute('SELECT value, created_at FROM cache WHERE key=?', (key,)).fetchone()
            if not row: return None
            if now-float(row[1]) > TTL_SECONDS:
                con.execute('DELETE FROM cache WHERE key=?', (key,)); con.commit(); return None
            try: return json.loads(row[0])
            except Exception: return None

    def _set(key,value):
        _init()
        with sqlite3.connect(DB_PATH) as con:
            con.execute('INSERT OR REPLACE INTO cache(key,value,created_at) VALUES(?,?,?)',(key,json.dumps(value,ensure_ascii=False),time.time()))
            con.commit()

    async def cache_get_json(key):
        return await asyncio.to_thread(_get,key)

    async def cache_set_json(key,value):
        await asyncio.to_thread(_set,key,value)
    ''').strip()+"\n")

# ---- main.py patches ----
p=root/'app/main.py'; s=p.read_text()
# locks
s=s.replace("TASKS: dict[str, asyncio.Task] = {}\n", "TASKS: dict[str, asyncio.Task] = {}\nJOB_STAT_LOCKS: dict[str, asyncio.Lock] = {}\n")
# Lock the two process_company job-stat read-modify-write blocks by precise snippets.
old="""    if tavily_spent:\n        job_now = await get_job(job_id)\n        st_now = (job_now or {}).get('stats') or {}\n        st_now['tavily_estimated_credits'] = int(st_now.get('tavily_estimated_credits', 0) or 0) + tavily_spent\n        await update_job(job_id, stats=st_now)\n"""
new="""    if tavily_spent:\n        lock=JOB_STAT_LOCKS.setdefault(job_id, asyncio.Lock())\n        async with lock:\n            job_now = await get_job(job_id)\n            st_now = (job_now or {}).get('stats') or {}\n            st_now['tavily_estimated_credits'] = int(st_now.get('tavily_estimated_credits', 0) or 0) + tavily_spent\n            await update_job(job_id, stats=st_now)\n"""
if old not in s: raise SystemExit('main stats block 1 not found')
s=s.replace(old,new)
old="""        job_now = await get_job(job_id)\n        st_now = (job_now or {}).get('stats') or {}\n        st_now['tavily_estimated_credits'] = int(st_now.get('tavily_estimated_credits', 0) or 0) + int(family_tavily or 0)\n        await update_job(job_id, stats=st_now)\n"""
new="""        if family_tavily:\n            lock=JOB_STAT_LOCKS.setdefault(job_id, asyncio.Lock())\n            async with lock:\n                job_now = await get_job(job_id)\n                st_now = (job_now or {}).get('stats') or {}\n                st_now['tavily_estimated_credits'] = int(st_now.get('tavily_estimated_credits', 0) or 0) + int(family_tavily or 0)\n                await update_job(job_id, stats=st_now)\n"""
if old not in s: raise SystemExit('main stats block 2 not found')
s=s.replace(old,new)
# strict saved quota and skip family search
old="""    saved_here=0\n    for _, info, raw_person in ranked:\n        if await job_stop_requested(job_id):\n"""
new="""    saved_here=0\n    max_people=int(cfg.get('max_people_per_company',25) or 25)\n    for _, info, raw_person in ranked:\n        # Hard persistence quota: this guard is intentionally independent of ranking/discovery.\n        if saved_here >= max_people:\n            await append_log(job_id, f'[{domain}] HARD QUOTA reached ({saved_here}/{max_people}); no more lead writes.', 'info')\n            break\n        if await job_stop_requested(job_id):\n"""
if old not in s: raise SystemExit('quota loop not found')
s=s.replace(old,new)
# Before family research, enforce no Tavily after quota.
needle="""    # Corporate family discovery never uses Seamless credits.\n    company_name = next((extract_person(r,domain,relationship,i,parent_domain=parent_domain,root_domain=root_domain)['company'] for _,i,r in ranked if r), domain)\n"""
replacement="""    # Corporate family research is also suppressed once this company's lead quota is full.\n    if saved_here >= max_people:\n        await sb_update('companies', {'id':company_id}, {'status':'done','evidence':[],'updated_at':__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()})\n        await append_log(job_id, f'[{domain}] Quota complete; skipping corporate-family Tavily research.', 'success')\n        return []\n\n    # Corporate family discovery never uses Seamless credits.\n    company_name = next((extract_person(r,domain,relationship,i,parent_domain=parent_domain,root_domain=root_domain)['company'] for _,i,r in ranked if r), domain)\n"""
if needle not in s: raise SystemExit('family skip needle not found')
s=s.replace(needle,replacement)

# Replace process_job whole function.
start=s.index('async def process_job(jid: str, cfg: dict):')
end=s.index('\n\nasync def resume_incomplete_jobs()', start)
new_job=r'''async def process_job(jid: str, cfg: dict):
    """Process root companies sequentially; process each family's discovered children concurrently.

    The frontier is confidence/queue-score ordered and capped by max_companies_per_family.
    A semaphore of three bounds public-search concurrency so five subsidiaries are processed
    in roughly two waves instead of five sequential network-heavy passes.
    """
    try:
        await update_job(jid,status='running')
        await append_log(jid,'Job started')
        pool=SeamlessPool()
        lead_cache=await load_lead_cache(jid)
        roots=unique_domains(cfg['websites'])
        existing=await sb_select('companies', params={'job_id':f'eq.{jid}','select':'*','order':'created_at.asc','limit':'5000'})
        by_root={}
        for c in existing or []:
            by_root.setdefault(c.get('root_domain') or '', []).append(c)
        cfg.setdefault('family_credit_usage',{})
        existing_job=await get_job(jid)
        existing_stats=(existing_job or {}).get('stats') or {}
        total_companies=int(existing_stats.get('companies_processed',0) or 0)
        total_credits=int(existing_stats.get('seamless_credits_used',0) or 0)
        total_leads=int(existing_stats.get('leads_saved',0) or 0)
        semaphore=asyncio.Semaphore(3)
        root_index=0

        for root in roots:
            if await job_stop_requested(jid):
                await mark_job_stopped(jid,'Stop requested — no new families will be started.')
                return
            root_index+=1
            rows=by_root.get(root,[])
            family_id=rows[0].get('family_id') if rows and rows[0].get('family_id') else str(uuid.uuid4())
            completed={c.get('domain') for c in rows if c.get('status') in ('done','done_no_people')}
            heap=[]; queued=set(); processed=set()
            seq=0

            def enqueue(domain, relationship, parent_domain, confidence=0.0, queue_score=100.0):
                nonlocal seq
                rd=norm_domain(domain)
                if not rd or rd in completed or rd in processed or rd in queued:
                    return False
                # Root always outranks children; subsidiary queue_score/confidence then decide order.
                priority=1000.0 if relationship=='original' else (float(queue_score or 0)+float(confidence or 0)*100.0)
                seq+=1
                import heapq
                heapq.heappush(heap, (-priority, seq, rd, relationship, parent_domain, float(confidence or 0), float(queue_score or 0)))
                queued.add(rd)
                return True

            # Resume any interrupted rows first, keeping a root row at the front when necessary.
            for c in rows:
                status=(c.get('status') or 'queued').lower()
                d=c.get('domain')
                if d and status in ('queued','processing','error','stopped'):
                    enqueue(d,c.get('relationship','related'),c.get('parent_domain'),0,100 if c.get('relationship')=='original' else 50)
            if not heap and root not in completed:
                enqueue(root,'original',None,1.0,1000)

            family_state={'credits_used':int((cfg.get('family_credit_usage') or {}).get(root,0) or 0),'leads_saved':0}
            family_companies=0
            last_domain=root
            await append_log(jid, f'Starting family {root} ({root_index}/{len(roots)}) — {len(heap)} queued.' if rows else f'Starting family {root} ({root_index}/{len(roots)})')

            async def run_one(item):
                _,_,domain,relationship,parent_domain,_,_=item
                async with semaphore:
                    local_state={'leads_saved':0,'credits_used':0}
                    before_leads=local_state['leads_saved']
                    related=[]
                    try:
                        await update_stats(jid, root_companies=len(roots), companies_processed=total_companies, current_company=domain, current_root=root, family_credits_used=family_state['credits_used'], leads_saved=total_leads)
                        related=await process_company(jid,family_id,root,domain,relationship,parent_domain,cfg,pool,local_state,lead_cache)
                        return domain, related, max(0,local_state.get('leads_saved',0)-before_leads), None
                    except Exception as e:
                        return domain, [], 0, e

            while heap and family_companies < int(cfg.get('max_companies_per_family',25) or 25):
                if await job_stop_requested(jid):
                    await mark_job_stopped(jid, f'Family {root} stopped by user.')
                    return
                import heapq
                wave=[]
                slots=max(1,min(3,int(cfg.get('max_companies_per_family',25) or 25)-family_companies))
                while heap and len(wave)<slots:
                    item=heapq.heappop(heap); queued.discard(item[2])
                    domain=item[2]
                    if domain in completed or domain in processed:
                        continue
                    processed.add(domain); family_companies+=1; total_companies+=1; last_domain=domain
                    wave.append(item)
                if not wave: break
                results=await asyncio.gather(*(run_one(x) for x in wave))
                for domain,related,added_leads,err in results:
                    total_leads+=added_leads
                    family_state['leads_saved']+=added_leads
                    if err:
                        await append_log(jid,f'{domain}: ERROR {err}','error')
                        row=await sb_select('companies',params={'job_id':f'eq.{jid}','domain':f'eq.{domain}','select':'id','limit':'1'})
                        if row:
                            await sb_update('companies',{'id':row[0]['id']},{'status':'error','updated_at':__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()})
                        continue
                    added=0
                    for rr in sorted(related or [], key=lambda x:(float(x.get('queue_score',0) or 0),float(x.get('confidence',0) or 0)), reverse=True):
                        rd=norm_domain(rr.get('domain',''))
                        conf=float(rr.get('confidence',0) or 0)
                        rel=(rr.get('relationship') or 'related').lower().strip()
                        qscore=float(rr.get('queue_score') or 0)
                        await save_relationship(jid,family_id,domain,rr)
                        min_conf=float(env('MIN_RELATIONSHIP_CONFIDENCE','0.72'))
                        min_q=float(env('MIN_RELATIONSHIP_QUEUE_SCORE','55'))
                        if not rd or rd==domain or rd in completed or rd in processed or rd in queued or conf < min_conf or qscore < min_q:
                            continue
                        if added >= int(cfg.get('max_related_per_company',6) or 6):
                            continue
                        if family_companies + len(heap) >= int(cfg.get('max_companies_per_family',25) or 25) and len(wave)==0:
                            break
                        if enqueue(rd,rel,domain,conf,qscore):
                            await find_or_create_company(jid,family_id,root,rd,rel,domain,rr.get('company') or rr.get('name'))
                            added+=1
                    await append_log(jid,f'{domain}: corporate research found {len(related or [])} candidates; queued {added}.','info')
                await update_stats(jid,root_companies=len(roots),companies_processed=total_companies,seamless_credits_used=total_credits,current_company=last_domain,current_root=root,leads_saved=total_leads)

            cfg['family_credit_usage'][root]=family_state['credits_used']
            await update_job(jid,config=cfg)
            await append_log(jid,f'Family {root} complete. Leads saved this family: {family_state.get("leads_saved",0)}.')

        job=await get_job(jid); st=job.get('stats') or {}
        st.update({'root_companies':len(roots),'companies_processed':total_companies,'seamless_credits_used':total_credits,'leads_saved':total_leads})
        await update_job(jid,status='completed',stats=st)
        await append_log(jid,'Job completed','success')
    except Exception as e:
        await update_job(jid,status='failed',error=str(e))
        await append_log(jid,f'JOB ERROR: {e}','error')
    finally:
        TASKS.pop(jid,None)
        JOB_STAT_LOCKS.pop(jid,None)
'''
s=s[:start]+new_job+s[end:]

# Add export-all formats while retaining original xlsx route.
insert_before='@app.get(\'/api/jobs/{jid}/export\')\n'
extra=r'''@app.get('/api/jobs/{jid}/export-all')
async def export_all(jid: str, _: bool = Depends(auth)):
    import zipfile
    job=await get_job(jid)
    if not job: raise HTTPException(404,'job not found')
    leads=await sb_select('leads',params={'job_id':f'eq.{jid}','select':'company,domain,root_domain,parent_domain,relationship,name,title,matched_title,title_score,similarity,linkedin,phone,email,source','order':'title_score.desc','limit':'5000'})
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
        # CSV
        csvbuf=io.StringIO(); fields=['company','domain','root_domain','parent_domain','relationship','name','title','matched_title','title_score','similarity','linkedin','phone','email','source']; w=csv.DictWriter(csvbuf,fieldnames=fields); w.writeheader(); w.writerows([{k:r.get(k,'') for k in fields} for r in leads]); z.writestr(f'prospecting_{jid[:8]}.csv',csvbuf.getvalue())
        # JSON
        z.writestr(f'prospecting_{jid[:8]}.json',json.dumps(leads,indent=2,ensure_ascii=False))
        # XLSX
        xbuf=await export_rows(jid); z.writestr(f'prospecting_{jid[:8]}.xlsx',xbuf.getvalue())
    out.seek(0)
    return StreamingResponse(out,media_type='application/zip',headers={'Content-Disposition':f'attachment; filename="prospecting_{jid[:8]}_exports.zip"'})

'''
if '@app.get(\'/api/jobs/{jid}/export-all\')' not in s:
    s=s.replace("@app.get('/api/jobs/{jid}/export')\n", extra+"@app.get('/api/jobs/{jid}/export')\n")
# Add score fill to Excel and autofilter as existing.
old="""    for r in leads: ws.append([r.get(x,'') for x in ['company','domain','root_domain','parent_domain','relationship','relationship_display','name','title','matched_title','title_score','similarity','linkedin','phone','email','source','seamless_key_index']])\n"""
new="""    for r in leads: ws.append([r.get(x,'') for x in ['company','domain','root_domain','parent_domain','relationship','relationship_display','name','title','matched_title','title_score','similarity','linkedin','phone','email','source','seamless_key_index']])\n    from openpyxl.styles import PatternFill, Font\n    for cell in ws[1]: cell.font=Font(bold=True)\n    score_col=10\n    for row in ws.iter_rows(min_row=2, min_col=score_col, max_col=score_col):\n        c=row[0]\n        try: v=float(c.value or 0)\n        except Exception: v=0\n        c.fill=PatternFill('solid', fgColor=('2DE2E6' if v>=85 else 'F5B642' if v>=60 else 'E05A5A'))\n        c.font=Font(bold=True, color='071015')\n"""
if old not in s: raise SystemExit('export lead append block not found')
s=s.replace(old,new)
p.write_text(s)

# ---- new minimal HTML using original JS, with a few patched functions ----
orig=(root/'web/index.html').read_text()
script=orig.split('<script>',1)[1].rsplit('</script>',1)[0]
# patch JS render/export/status/log functions
script=script.replace("function exportCurrent(){if(selected)window.location=`/api/jobs/${selected}/export`;else toast('Select a run first.');}", r"""function exportCurrent(){if(selected)window.location=`/api/jobs/${selected}/export-all`;else toast('Select a run first.');}
function exportFormat(fmt){if(!selected){toast('Select a run first.');return;} window.location=`/api/jobs/${selected}/export?format=${encodeURIComponent(fmt)}`;}
function toggleAdvanced(force){const m=$('advancedModal');if(!m)return;const open=typeof force==='boolean'?force:!m.classList.contains('open');m.classList.toggle('open',open);m.setAttribute('aria-hidden',String(!open));}
function closeAdvanced(ev){if(ev.target===$('advancedModal')) toggleAdvanced(false);}
function setGlobalStatus(status){const e=$('globalStatus');if(!e)return;const s=String(status||'idle').toLowerCase();const label=s==='running'||s==='queued'||s==='stopping'?'RUNNING':s==='failed'?'STOPPED':s==='completed'?'IDLE':'IDLE';e.textContent=(label==='RUNNING'?'🟡 ':label==='STOPPED'?'🔴 ':'🟢 ')+label;e.className='status-pill '+label.toLowerCase();}
function clearLog(){const e=$('diagEvents');if(e)e.innerHTML='<div class="log-empty">Log display cleared. Database events remain stored.</div>';}
function exportLog(){const logs=(currentDetail&&currentDetail.logs)||[];const text=logs.map(l=>`[${new Date(l.created_at||Date.now()).toLocaleTimeString()}] [${String(l.level||'INFO').toUpperCase()}] → ${l.message||''}`).join('\\n');const blob=new Blob([text],{type:'text/plain'});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=`prospecting_${(selected||'job').slice(0,8)}_log.txt`;a.click();URL.revokeObjectURL(a.href);}""")
script=script.replace("$('detailTitle').textContent=`Run ${(job.id||'unknown').slice(0,8)} · ${(job.mode||'').toUpperCase()}`;", "$('detailTitle').textContent=`Run ${(job.id||'unknown').slice(0,8)} · ${(job.mode||'').toUpperCase()}`; setGlobalStatus(job.status||'idle');")
# replace renderDiagnostics to add terminal log classes
start=script.find('function renderDiagnostics('); end=script.find('\nfunction toggleCompanies',start)
new_diag=r'''function renderDiagnostics(d){const job=d?.job||{};const logs=Array.isArray(d?.logs)?d.logs:[];const leads=Array.isArray(d?.leads)?d.leads:[];const err=job.error||'';const last=logs.slice(-1)[0];$('detailDiag').innerHTML=`<div class="diag-grid"><div><span>Status</span><b class="status ${esc(job.status||'unknown')}">${esc(job.status||'unknown')}</b></div><div><span>Current</span><b>${fmt(job.stats?.current_company)}</b></div><div><span>Leads</span><b>${leads.length}</b></div><div><span>Last event</span><b>${fmt(last?.message)}</b></div></div>${err?`<div class="errorbox"><b>Job error</b><pre>${esc(err)}</pre></div>`:''}`;$('diagEvents').innerHTML=logs.map(l=>`<div class="terminal-line ${esc(l.level||'info')}"><span>[${new Date(l.created_at||Date.now()).toLocaleTimeString([], {hour12:false})}]</span><b>[${esc(String(l.level||'info').toUpperCase())}]</b><p>→ ${esc(l.message||'')}</p></div>`).join('')||'<div class="log-empty">Waiting for scan output…</div>';$('diagRaw').textContent=JSON.stringify(d,null,2);const log=$('diagEvents');if(log)log.scrollTop=log.scrollHeight;}
'''
script=script[:start]+new_diag+script[end:]
# Modify initApp to support modal escape and advanced defaults.
script=script.replace("function initApp(){ const ex=$('excluded'); if(ex) ex.addEventListener('input',updateExcludedCount); updateExcludedCount(); checkHealth(); loadJobs(); }", "function initApp(){ const ex=$('excluded'); if(ex) ex.addEventListener('input',updateExcludedCount); updateExcludedCount(); checkHealth(); loadJobs(); setGlobalStatus('idle'); document.addEventListener('keydown',e=>{if(e.key==='Escape')toggleAdvanced(false)}); }")

html=f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Prospecting Beast // OSINT Terminal</title><link rel="stylesheet" href="/static/app.css"></head><body><div class="app-shell">
<header class="topbar"><div class="brand"><div class="brand-mark">PB</div><div><div class="brand-name">Prospecting Beast</div><div class="brand-sub">ZERO-CONFIG OSINT TERMINAL</div></div></div><div id="globalStatus" class="status-pill idle">🟢 IDLE</div><div class="top-actions"><button class="icon-btn" onclick="toggleAdvanced()" title="Advanced settings">⚙️ <span>Advanced</span></button><button class="export-top" onclick="exportCurrent()">📤 Export All</button><span id="clock" class="clock">--:--:--</span></div></header>
<main class="workspace"><section class="main-column">
<section class="scan-zone"><div class="scan-copy"><div class="eyebrow">TARGET INPUT</div><h1>Scan a company. Get the people.</h1><p>Paste one or more domains. Prospecting Beast handles discovery, scoring and corporate-family research automatically.</p></div><div class="scan-row"><textarea id="sites" class="domain-input" placeholder="Paste your target domains here... (e.g., microsoft.com, amazon.com)"></textarea><button class="scan-btn" onclick="startJob()">SCAN</button></div><div id="quickResult" class="quick-status"></div><div class="stage-strip"><div id="stageDiscovery" class="stage"><span>01</span>DISCOVER</div><div id="stageScoring" class="stage"><span>02</span>SCORE</div><div id="stageFamily" class="stage"><span>03</span>FAMILY</div><div id="stageEnrich" class="stage"><span>04</span>ENRICH</div></div></section>
<section class="results-panel"><div class="results-head"><div><div class="eyebrow">ACTIONABLE INTELLIGENCE</div><h2>Prospects</h2></div><div class="results-tools"><input id="leadFilter" placeholder="filter by name / company..." oninput="filterLeads()"><select id="scoreFilter" onchange="filterLeads()"><option value="0">All scores</option><option value="90">90+</option><option value="80">80+</option><option value="70">70+</option></select><button id="enrichSelectedBtn" class="primary" onclick="enrichSelected()" disabled>⚡ Enrich Selected (0)</button><button onclick="selectAllVisible()">Select All</button><button onclick="clearLeadSelection()">Clear</button></div></div><div id="resultsMeta" class="result-meta">No run selected.</div><div id="selectedRunBar" class="selected-run-bar hidden"></div><div class="tablewrap"><table><thead><tr><th><input id="selectAllLeads" type="checkbox" onchange="toggleSelectAllVisible(this.checked)"></th><th>Score</th><th>Candidate</th><th>Matched Role</th><th>Status</th><th>Contact Info</th><th>Company</th><th>🔗 Link</th></tr></thead><tbody id="leadRows"><tr><td colspan="8" class="empty-cell">Paste a domain above and press SCAN.</td></tr></tbody></table></div></section>
</section>
<aside class="log-panel"><div class="log-head"><div><div class="eyebrow">LIVE LOG</div><strong>Terminal</strong></div><div class="log-actions"><button onclick="clearLog()">Clear</button><button onclick="exportLog()">Export Log</button></div></div><div id="diagEvents" class="diag-events"><div class="log-empty">Waiting for scan output…</div></div></aside>
</main>
<div id="advancedModal" class="modal" aria-hidden="true" onclick="closeAdvanced(event)"><div class="modal-card"><div class="modal-head"><div><div class="eyebrow">ADVANCED</div><h2>Operator controls</h2></div><button class="modal-close" onclick="toggleAdvanced(false)">×</button></div><div class="advanced-grid">
<div class="settings-block"><div class="block-title">Search</div><div class="two-col"><label>Role Mode<select id="mode"><option value="f">F</option><option value="nf">NF</option><option value="both">Both</option></select></label><label>Max People / Company<input id="maxp" type="number" value="25" min="1" max="100"></label><label>Web Search Budget<input id="webq" type="number" value="6" min="1" max="16"></label><label>Web Minimum Qualified<input id="webmin" type="number" value="5" min="0" max="50"></label></div></div>
<div class="settings-block"><div class="block-title">Context</div><div class="two-col"><label>Location<input id="location" placeholder="London, UK"></label><label>Industry<input id="industry" placeholder="Fintech"></label><label>Search Provider<select id="osint"><option value="web">WEB</option><option value="web+osint">WEB + PASSIVE OSINT</option></select></label><label>Job Tavily Budget<input id="tavilyBudget" type="number" value="40" min="1" max="500"></label></div></div>
<div class="settings-block"><div class="block-title">Family + execution</div><div class="two-col"><label>Max Related / Company<input id="maxr" type="number" value="6" min="0" max="50"></label><label>Max Companies / Family<input id="maxc" type="number" value="25" min="1" max="500"></label><label>Family Seamless Budget<input id="maxe" type="number" value="20" min="0" max="1000"></label><label class="check"><input id="dry" type="checkbox" checked> Dry Run</label></div><div class="checks"><label class="check"><input id="agentic" type="checkbox"> Agentic planner</label><label class="check"><input id="spiderfoot" type="checkbox"> SpiderFoot</label><label class="check"><input id="amass" type="checkbox"> Amass passive</label></div></div>
<div class="settings-block full"><div class="block-title">Excluded Contacts</div><textarea id="excluded" placeholder="Name | Email | LinkedIn (one per line)"></textarea><div id="excludedCount" class="muted">0 people</div></div>
<div class="settings-block full research"><div class="block-title">Research tools</div><div class="tool-row"><input id="toolLocation" placeholder="Location"><input id="toolIndustry" placeholder="Industry"><button onclick="generateXray()">Generate X-Ray</button></div><textarea id="xrayOutput" placeholder="Queries…" readonly></textarea><div class="tool-row"><input id="emailFirst" placeholder="First name"><input id="emailLast" placeholder="Last name"><input id="emailDomain" placeholder="company.com"><button onclick="generateEmails()">Email Candidates</button></div><div id="emailOutput" class="tool-output">No email check yet.</div><textarea id="parseUrls" placeholder="Public URLs, one per line"></textarea><button onclick="parsePublicPages()">Parse Public Pages</button><div id="parseOutput" class="tool-output">No parse yet.</div></div>
<div class="settings-block full"><div class="block-title">Run History</div><div class="job-toolbar"><button onclick="loadJobs()">↻ Refresh</button><button id="globalStop" class="danger" onclick="stopSelectedJob()" disabled>■ Stop</button><button onclick="archiveEmptyJobs()">Clean Empty</button><label class="check"><input id="showArchived" type="checkbox" onchange="loadJobs()"> Archived</label></div><div id="jobs" class="jobs">Loading…</div></div>
</div></div></div>
<div id="systemList" hidden></div><div id="companyArea" class="hidden"></div><div id="companyRows" hidden></div><div id="relationshipRows" hidden></div><div id="companyToggle" hidden></div><div id="detailTitle" hidden></div><div id="jobActions" hidden></div><div id="detailDiag" hidden></div><div id="diagRaw" hidden></div><div id="toast" class="toast"></div>
<script>{script}</script></body></html>'''
(root/'web/index.html').write_text(html)

# CSS
(root/'static/app.css').write_text(textwrap.dedent(r'''
:root{--bg:#071015;--panel:#0b151b;--panel2:#0e1b22;--line:#1b3038;--muted:#71858e;--text:#eaf5f7;--cyan:#2de2e6;--cyan2:#9efcff;--green:#45e08a;--amber:#f5b642;--red:#e05a5a;--radius:14px;--mono:"JetBrains Mono",ui-monospace,SFMono-Regular,Consolas,monospace;--sans:Inter,ui-sans-serif,system-ui,-apple-system,Segoe UI,sans-serif}*{box-sizing:border-box}html,body{margin:0;min-height:100%;background:radial-gradient(circle at 10% 0,#0b1b21 0,#071015 45%,#050b0f 100%);color:var(--text);font-family:var(--sans)}body:before{content:"";position:fixed;inset:0;pointer-events:none;background:linear-gradient(rgba(255,255,255,.02) 1px,transparent 1px);background-size:100% 4px;opacity:.12}.app-shell{min-height:100vh}.topbar{height:72px;padding:0 26px;border-bottom:1px solid var(--line);display:grid;grid-template-columns:1fr auto 1fr;align-items:center;gap:20px;position:sticky;top:0;z-index:20;background:rgba(7,16,21,.92);backdrop-filter:blur(14px)}.brand{display:flex;align-items:center;gap:12px}.brand-mark{font:800 18px var(--mono);border:1px solid var(--cyan);box-shadow:0 0 18px rgba(45,226,230,.18);color:var(--cyan);padding:7px 9px;border-radius:8px}.brand-name{font-size:14px;font-weight:800;letter-spacing:.06em}.brand-sub{font:10px var(--mono);color:var(--muted);margin-top:2px}.status-pill{justify-self:center;padding:8px 14px;border:1px solid var(--line);border-radius:999px;font:700 11px var(--mono);letter-spacing:.12em;background:#09161b}.status-pill.running{color:var(--amber);border-color:rgba(245,182,66,.35)}.status-pill.idle{color:var(--green);border-color:rgba(69,224,138,.25)}.status-pill.stopped{color:var(--red);border-color:rgba(224,90,90,.3)}.top-actions{justify-self:end;display:flex;align-items:center;gap:8px}.top-actions button,.icon-btn,.export-top{border:1px solid var(--line);background:#0c181e;color:var(--text);padding:9px 11px;border-radius:9px;font:600 11px var(--mono);cursor:pointer}.top-actions button:hover{border-color:#2a505b}.export-top{border-color:rgba(45,226,230,.35);color:var(--cyan)}.clock{font:700 11px var(--mono);color:var(--muted);min-width:72px;text-align:right}.workspace{display:grid;grid-template-columns:minmax(0,7fr) minmax(320px,3fr);min-height:calc(100vh - 72px)}.main-column{padding:22px 20px 30px 26px;min-width:0}.scan-zone{padding:24px;border:1px solid var(--line);background:linear-gradient(145deg,rgba(14,27,34,.95),rgba(8,16,21,.95));border-radius:var(--radius);box-shadow:0 18px 50px rgba(0,0,0,.24)}.eyebrow{font:700 10px var(--mono);letter-spacing:.16em;color:var(--cyan);text-transform:uppercase}.scan-copy h1{font-size:29px;line-height:1.05;margin:8px 0}.scan-copy p{color:var(--muted);max-width:720px;margin:0 0 18px}.scan-row{display:grid;grid-template-columns:1fr 148px;gap:12px;align-items:stretch}.domain-input{min-height:120px;resize:vertical;padding:17px;background:#061015;border:1px solid #24404a;border-radius:12px;color:var(--text);font:600 14px/1.6 var(--mono);outline:none}.domain-input:focus{border-color:var(--cyan);box-shadow:0 0 0 3px rgba(45,226,230,.08)}.scan-btn{border:1px solid var(--cyan);border-radius:12px;background:linear-gradient(180deg,#1ff2f5,#14c5cc);color:#041013;font:900 18px var(--mono);letter-spacing:.08em;cursor:pointer;box-shadow:0 0 28px rgba(45,226,230,.22)}.scan-btn:hover{filter:brightness(1.05);transform:translateY(-1px)}.quick-status{margin-top:12px;min-height:38px;border:1px solid var(--line);border-radius:10px;padding:9px 12px;color:var(--muted);font:600 11px var(--mono);background:rgba(7,16,21,.65)}.quick-status.good{border-color:rgba(69,224,138,.25);color:var(--green)}.quick-status.bad{border-color:rgba(224,90,90,.25);color:#ff8f8f}.stage-strip{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:12px}.stage{padding:8px 10px;border:1px solid var(--line);border-radius:8px;color:#526872;font:700 9px var(--mono);letter-spacing:.1em}.stage span{margin-right:7px}.stage.active{color:var(--amber);border-color:rgba(245,182,66,.25);background:rgba(245,182,66,.05)}.stage.done{color:var(--green);border-color:rgba(69,224,138,.2);background:rgba(69,224,138,.04)}.stage.error{color:var(--red);border-color:rgba(224,90,90,.2)}.results-panel{margin-top:16px;border:1px solid var(--line);border-radius:var(--radius);background:rgba(8,17,22,.9);overflow:hidden}.results-head{display:flex;justify-content:space-between;align-items:center;gap:16px;padding:16px 18px;border-bottom:1px solid var(--line)}.results-head h2{margin:3px 0 0;font-size:21px}.results-tools{display:flex;gap:7px;align-items:center;flex-wrap:wrap;justify-content:flex-end}.results-tools input,.results-tools select,.results-tools button,.job-toolbar button{background:#0a171d;border:1px solid var(--line);color:var(--text);border-radius:8px;padding:8px 10px;font:600 10px var(--mono)}.results-tools input{width:200px}.results-tools input:focus,.results-tools select:focus{outline:none;border-color:#2d5964}.results-tools button{cursor:pointer}.results-tools .primary{color:#061014;background:var(--cyan);border-color:var(--cyan)}.result-meta{padding:9px 18px;color:var(--muted);font:600 10px var(--mono)}.selected-run-bar{margin:0 18px 10px;padding:8px 10px;border:1px solid rgba(45,226,230,.2);border-radius:8px;color:var(--cyan2);background:rgba(45,226,230,.03);font:700 10px var(--mono)}.hidden{display:none!important}.tablewrap{overflow:auto;max-height:calc(100vh - 370px)}table{width:100%;border-collapse:separate;border-spacing:0;font-size:11px}thead th{position:sticky;top:0;background:#0b171d;color:#78909a;text-align:left;font:800 9px var(--mono);letter-spacing:.08em;padding:11px 10px;border-bottom:1px solid var(--line);z-index:2}tbody td{padding:11px 10px;border-bottom:1px solid rgba(27,48,56,.55);vertical-align:top}tbody tr:hover{background:rgba(45,226,230,.025)}tbody tr.selected-lead-row{background:rgba(45,226,230,.05)}td b{font-size:11px}td small{display:block;color:#647985;font:500 9px/1.5 var(--mono);margin-top:3px;max-width:260px}.score{display:inline-grid;place-items:center;min-width:38px;padding:5px 6px;border-radius:7px;font:800 10px var(--mono)}.score.high{background:rgba(69,224,138,.11);color:var(--green)}.score.mid{background:rgba(245,182,66,.11);color:var(--amber)}.score.low{background:rgba(224,90,90,.1);color:#ff8f8f}.enrich-status{display:inline-block;padding:5px 7px;border-radius:999px;border:1px solid var(--line);font:700 9px var(--mono)}.enrich-status.enriched{color:var(--green);border-color:rgba(69,224,138,.25)}.enrich-status.failed{color:#ff8f8f;border-color:rgba(224,90,90,.25)}.enrich-status.skipped{color:var(--muted)}.enrich-status.ready{color:var(--cyan)}.enrich-status.running{color:var(--amber)}.enrich-status.quota{color:var(--amber)}.empty-cell{padding:34px!important;color:#5f737d;text-align:center;font:600 11px var(--mono)}.log-panel{border-left:1px solid var(--line);background:#040b0f;min-height:calc(100vh - 72px);display:flex;flex-direction:column;position:sticky;top:72px;height:calc(100vh - 72px)}.log-head{height:58px;padding:0 14px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between}.log-actions{display:flex;gap:5px}.log-actions button{border:1px solid var(--line);background:#091218;color:#6d8791;padding:6px 8px;border-radius:7px;font:700 9px var(--mono);cursor:pointer}.diag-events{padding:12px;overflow:auto;flex:1;font:600 10px/1.55 var(--mono)}.terminal-line{display:grid;grid-template-columns:auto auto 1fr;gap:6px;padding:2px 0}.terminal-line span{color:#465e67}.terminal-line b{color:var(--cyan)}.terminal-line.success b{color:var(--green)}.terminal-line.warning b{color:var(--amber)}.terminal-line.error b{color:var(--red)}.terminal-line p{margin:0;color:#b2c4c9;word-break:break-word}.log-empty{color:#3f5963;padding:20px 4px;text-align:center}.modal{position:fixed;inset:0;z-index:50;background:rgba(1,5,7,.76);backdrop-filter:blur(10px);display:none;place-items:center;padding:24px}.modal.open{display:grid}.modal-card{width:min(960px,100%);max-height:88vh;overflow:auto;border:1px solid #26424b;border-radius:16px;background:#081217;box-shadow:0 30px 90px rgba(0,0,0,.55)}.modal-head{display:flex;justify-content:space-between;align-items:center;padding:18px 20px;border-bottom:1px solid var(--line);position:sticky;top:0;background:#081217;z-index:2}.modal-head h2{margin:4px 0 0;font-size:19px}.modal-close{border:0;background:none;color:#607983;font-size:24px;cursor:pointer}.advanced-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;padding:16px}.settings-block{border:1px solid var(--line);border-radius:12px;padding:13px;background:#09161c}.settings-block.full{grid-column:1/-1}.block-title{font:800 10px var(--mono);color:var(--cyan);letter-spacing:.12em;margin-bottom:9px;text-transform:uppercase}.two-col{display:grid;grid-template-columns:1fr 1fr;gap:9px}.two-col label{font:700 9px var(--mono);color:#79909a}.two-col input,.two-col select,.settings-block textarea,.tool-row input,.research textarea,.tool-output{margin-top:5px;width:100%;border:1px solid var(--line);border-radius:8px;background:#061015;color:var(--text);padding:9px;font:600 10px var(--mono);outline:none}.settings-block textarea{min-height:82px;resize:vertical}.checks{display:flex;gap:14px;margin-top:10px;flex-wrap:wrap}.check{font:700 9px var(--mono);color:#7f959e}.check input{accent-color:var(--cyan)}.tool-row{display:grid;grid-template-columns:1fr 1fr auto;gap:7px;margin-bottom:7px}.research button,.job-toolbar button{cursor:pointer}.research textarea{min-height:70px;resize:vertical}.research .tool-output{min-height:40px;margin-bottom:8px}.job-toolbar{display:flex;align-items:center;gap:7px;margin-bottom:9px;flex-wrap:wrap}.job-toolbar .danger{color:#ff9a9a;border-color:rgba(224,90,90,.25)}.jobs{display:grid;gap:5px;max-height:240px;overflow:auto}.job-item{width:100%;text-align:left;border:1px solid var(--line);background:#071117;color:var(--text);border-radius:8px;padding:9px;cursor:pointer}.job-item:hover,.job-item.selected-job{border-color:#2a505b;background:#0b1a21}.job-item-top,.job-item-meta{display:flex;justify-content:space-between;gap:7px}.job-item-top b{font:800 10px var(--mono);color:var(--cyan)}.job-item-top span,.job-item-meta span{font:700 9px var(--mono);color:#627983}.job-item-meta{margin-top:5px}.status-badge{color:var(--green)!important}.errorbox{border:1px solid rgba(224,90,90,.25);background:rgba(224,90,90,.04);padding:8px;border-radius:8px;color:#ff9a9a}.toast{position:fixed;left:50%;bottom:20px;transform:translate(-50%,20px);opacity:0;pointer-events:none;background:#0e1b22;border:1px solid var(--line);padding:10px 14px;border-radius:9px;font:700 10px var(--mono);transition:.2s;z-index:100}.toast.show{opacity:1;transform:translate(-50%,0)}.toast.good{color:var(--green);border-color:rgba(69,224,138,.25)}@media(max-width:980px){.workspace{grid-template-columns:1fr}.log-panel{position:relative;top:0;min-height:320px;height:320px;border-left:0;border-top:1px solid var(--line)}.main-column{padding:14px}.topbar{grid-template-columns:1fr auto;height:auto;padding:12px}.status-pill{display:none}.advanced-grid{grid-template-columns:1fr}.settings-block.full{grid-column:auto}.two-col{grid-template-columns:1fr}.scan-row{grid-template-columns:1fr}.scan-btn{height:54px}.tablewrap{max-height:none}.results-head{align-items:flex-start;flex-direction:column}.results-tools{justify-content:flex-start}}
''').strip()+"\n")

print('rebuild complete')
