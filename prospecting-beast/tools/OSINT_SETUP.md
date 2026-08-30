# Optional passive OSINT tools

## SpiderFoot
Run a SpiderFoot instance you control and set `SPIDERFOOT_ENABLED=true` and `SPIDERFOOT_URL=http://127.0.0.1:5001` (or your remote private URL). The app requests passive scans only. SpiderFoot exposes a REST API for starting scans and retrieving scan event results.

## Amass
Install Amass locally/inside your own worker and set `AMASS_ENABLED=true`. The app uses only `amass enum --passive -d DOMAIN -silent` for domain clues; it does not perform active recon.
