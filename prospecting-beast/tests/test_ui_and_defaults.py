from pathlib import Path
from bs4 import BeautifulSoup
from app.main import JobIn

BASE=Path(__file__).resolve().parents[1]

def test_default_people_target_is_20():
    j=JobIn(websites=['example.com'])
    assert j.max_people_per_company == 20

def test_people_target_can_be_increased():
    j=JobIn(websites=['example.com'], max_people_per_company=100)
    assert j.max_people_per_company == 100

def test_command_deck_has_unique_dom_ids():
    html=(BASE/'web'/'index.html').read_text(encoding='utf-8')
    soup=BeautifulSoup(html,'html.parser')
    ids=[x.get('id') for x in soup.find_all(attrs={'id':True})]
    dup=sorted({x for x in ids if ids.count(x)>1})
    assert not dup, f'duplicate DOM ids: {dup}'
    required=['sites','maxp','launchTarget','systemTrigger','jobs','leadRows','companyRows','relationshipRows','progressBar','companyProgressBar']
    for rid in required:
        assert soup.find(id=rid), f'missing #{rid}'
