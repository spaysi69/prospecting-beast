import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import asyncio
from app import core


def test_web_discovery_stops_before_tavily_when_quota_reached(monkeypatch):
    calls = []
    async def fake_crawl(domain, limit=8):
        return [{'url':'https://example.com/team','title':'Team','content':'team'}]
    async def fake_gemini(company, domain, evidence, mode):
        return [{'name':'Jane Doe','title':'IT Manager','linkedin_url':'https://linkedin.com/in/jane'}]
    async def fake_tavily(*args, **kwargs):
        calls.append(args[0]);
        return {'results': []}
    monkeypatch.setattr(core, 'crawl_public_company_pages', fake_crawl)
    monkeypatch.setattr(core, 'gemini_extract_people', fake_gemini)
    monkeypatch.setattr(core, 'tavily_search', fake_tavily)
    monkeypatch.setattr(core, 'spiderfoot_osint', lambda domain: asyncio.sleep(0, result=[]))
    monkeypatch.setattr(core, 'amass_passive_osint', lambda domain: asyncio.sleep(0, result=[]))
    out = asyncio.run(core.web_people_discovery('Example','example.com','f',max_people=1,search_budget=8))
    assert len(out['people']) == 1
    assert calls == []


def test_web_discovery_stops_after_batch_hits_quota(monkeypatch):
    calls = []
    async def fake_crawl(domain, limit=8): return []
    async def fake_gemini(company, domain, evidence, mode):
        return [{'name':'Jane Doe','title':'IT Manager','linkedin_url':'https://linkedin.com/in/jane'}]
    async def fake_tavily(*args, **kwargs):
        calls.append(args[0]); return {'results': [{'url':'https://example.com/jane','title':'Jane','content':'Jane Doe IT Manager','score':0.9}]}
    monkeypatch.setattr(core, 'crawl_public_company_pages', fake_crawl)
    monkeypatch.setattr(core, 'gemini_extract_people', fake_gemini)
    monkeypatch.setattr(core, 'tavily_search', fake_tavily)
    monkeypatch.setattr(core, 'spiderfoot_osint', lambda domain: asyncio.sleep(0, result=[]))
    monkeypatch.setattr(core, 'amass_passive_osint', lambda domain: asyncio.sleep(0, result=[]))
    out = asyncio.run(core.web_people_discovery('Example','example.com','f',max_people=1,search_budget=8))
    assert len(out['people']) == 1
    assert len(calls) == 1
