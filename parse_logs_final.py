import sys, json
lines = sys.stdin.readlines()
events, coins = [], set()
for l in lines:
    try:
        raw = l.split('| ',1)[1] if '| ' in l else l
        d = json.loads(raw)
        e = d.get('event','')
        if e == 'coin_regime_evaluated':
            sym = d.get('symbol','')
            if sym not in coins:
                coins.add(sym)
                events.append(d)
        elif e in ('crypto_dynamic_universe','market_scan_done','universe_generated',
                   'tradfi_perps_excluded','universe_profile_applied','universe_profile_read_failed'):
            events.append(d)
    except: pass

prof = [e for e in events if e.get('event')=='universe_profile_applied']
if prof: print(f"Profile: {prof[-1].get('profile')} | top_n={prof[-1].get('top_n')} | min_vol={prof[-1].get('min_vol_usd')}")

err = [e for e in events if e.get('event')=='universe_profile_read_failed']
if err: print(f"Profile error: {err[-1].get('error')}")

gen = [e for e in events if e.get('event')=='universe_generated']
if gen: print(f"Universe: {gen[-1].get('count')} coins")

tradfi = [e for e in events if e.get('event')=='tradfi_perps_excluded']
if tradfi: print(f"TradFi blocked: {tradfi[-1].get('count')}")

regime_coins = [e for e in events if e.get('event')=='coin_regime_evaluated']
dead = [e['symbol'] for e in regime_coins if e.get('state')=='DEAD_CHOP']
active = [e for e in regime_coins if e.get('state')!='DEAD_CHOP']
tradfi_leak = [e['symbol'] for e in active if e['symbol'] in ('XAUUSDT','XAGUSDT','SKHYNIXUSDT')]

print(f'\nTotal scanned: {len(regime_coins)} | Active: {len(active)} | DEAD_CHOP: {len(dead)}')
if tradfi_leak: print(f'  !! TradFi still leaking: {tradfi_leak}')
else: print('  TradFi: CLEAN')
print('  Top coins by ADX:')
for r in sorted(active, key=lambda x: x.get('adx_4h',0), reverse=True)[:25]:
    flag = ' <<< TOP GAINER' if r['symbol'] in ('TUSDT','ENAUSDT','DOGEUSDT') else ''
    print(f"    {r['symbol']:22s} {r['state']:28s} adx4h={r.get('adx_4h')}{flag}")

done = [e for e in events if e.get('event')=='market_scan_done']
if done:
    total_sig = sum(d.get('signals',0) for d in done)
    total_tick = sum(d.get('tickers',0) for d in done)
    print(f'\nScan: {total_tick} tickers | {total_sig} signals | {len(done)} batches')
