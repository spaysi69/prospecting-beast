import os, sys, json, re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core import norm_domain, title_dictionary, base_score
from app.osint_tools import email_variants, valid_email_syntax, extract_public_emails, parse_public_people, PublicPage

def test_domain(): assert norm_domain("https://www.example.com/foo") == "example.com"

def test_titles(): assert "IT Manager" in title_dictionary("f"); assert "CEO" in title_dictionary("nf")

def test_mapping():
    info=base_score("Director of Information Technology","f")
    assert info and info["matched_title"] in {"IT Manager","IT Director"}

def test_email():
    xs=email_variants("John","Smith","example.com"); assert xs and all(valid_email_syntax(x) for x in xs)

def test_extract_emails(): assert "john@example.com" in extract_public_emails("Contact john@example.com now")

def test_empty_page():
    p=PublicPage("https://example.com","Example","",[])
    assert parse_public_people(p)==[]
