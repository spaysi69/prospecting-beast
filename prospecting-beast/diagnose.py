import os, asyncio
from dotenv import load_dotenv
from app.core import supabase_enabled, sb_select, SeamlessPool, env

async def main():
    load_dotenv()
    print('Prospecting Beast configuration check\n')
    checks = [
        ('APP_PASSWORD', bool(env('APP_PASSWORD'))),
        ('SUPABASE_URL', bool(env('SUPABASE_URL'))),
        ('SUPABASE_SERVICE_ROLE_KEY', bool(env('SUPABASE_SERVICE_ROLE_KEY'))),
        ('APOLLO_API_KEY', bool(env('APOLLO_API_KEY'))),
        ('TAVILY_API_KEY', bool(env('TAVILY_API_KEY'))),
        ('GEMINI_API_KEY', bool(env('GEMINI_API_KEY'))),
    ]
    for name, ok in checks:
        print(f'[{'OK' if ok else 'MISSING':7}] {name}')
    pool=SeamlessPool(); print(f'[INFO   ] Seamless keys configured: {len(pool.keys)}')
    if supabase_enabled():
        try:
            await sb_select('jobs', params={'select':'id','limit':'1'})
            print('[OK     ] Supabase connection')
        except Exception as e:
            print('[ERROR  ] Supabase connection:', e)
    else:
        print('[SKIP   ] Supabase connection')
    print('\nThis diagnostic does NOT call Apollo, Tavily, Gemini, or Seamless enrichment, so it does not intentionally spend prospecting credits.')

if __name__ == '__main__':
    asyncio.run(main())
