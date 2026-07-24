import requests
import time
import datetime
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from datetime import timezone, timedelta

# --- Configuration ---
TELEGRAM_BOT_TOKEN = '8506264349:AAE2mayI7IdJOFob3_sZoBK0-ogs45sMIJQ'
TELEGRAM_CHAT_ID = '1692583809'

ALERTED_FILE = "alerted_coins.json"
PC_ALERT = 1.99
RSI_PERIOD = 14
RSI_MIN = 44.0
RSI_MAX = 65.0

# New filters based on today's data analysis
MIN_DAY_PC = -8.0      # Allow coins down up to -8% (reversal plays)
MAX_DAY_PC = 6.0       # Skip coins already up +6% or more
MIN_VOL_RATIO = 1.4    # Minimum volume ratio
MAX_VOL_RATIO = 8.2    # Skip if vol_ratio > 8.2 (overextended)
MAX_STREAK_RATIO = 25.0 # Skip if streak_ratio > 25 (exhausted)

RAW_SYMBOLS = """
0G 1000CAT 1000CHEEMS 1000SATS 1INCH 1MBABYDOGE 2Z AAVE ACE ACH ACM ACX ADA ADX AEVO AGLD AIGENSYN AI AIXBT ALGO ALICE ALLO ALPINE ALT AMP ANIME ANKR APE API3 APT ARB ARKM ARPA AR ASR ASTER ASTR ATM ATOM AT AUCTION AUDIO A AVA AVAX AVNT AXL AXS BABY BANANAS31 BANANA BAND BANK BARD BAR BAT BB BCH BEAMX BEL BERA BICO BIGTIME BIO BLUR BMT BNB BNSOL BNT BOME BONK BREV BROCCOLI714 C98 CAKE CATI CELO CELR CETUS CFG CFX CGPT CHIP CHR CHZ CITY CKB COMP COTI COW CRV CTK CTSI C CVC CVX CYBER DASH DCR DEXE DGB DIA DGB DOGE DOGS DOLO DOT DUSK DYDX DYM EDEN EDU EGLD EIGEN ENA ENJ ENSO ENS ETC EUL FET FF FIDA FIL FLOKI FLOW FLUX FOGO FORM FRAX F GALA GAS GENIUS GIGGLE GLMR GLM GMT GMX GNO GNS GPS GRT GUN G HAEDAL HBAR HEMI HIVE HMSTR HOLO HOME HOT HUMA HYPER ICP ICX ID ILV IMX INIT INJ IOST IOTA IOTX IO IQ JOE JST JTO JUP JUV KAIA KAITO KAT KAVA KERNEL KGST KITE KMNO KNC KSM LA LAYER LAZIO LDO LINEA LINK LISTA LPT LQTY LSK LTC LUMIA LUNA LUNC MAGIC MANA MANTA MANTRA MASK MAV MBL MEGA MEME METIS MET ME MINA MIRA MITO MMT MORPHO MOVR MTL MUBARAK NEAR NEIRO NEO NEWT NEXO NIGHT NIL NMR NOT NXPC OGN OG ONDO ONE ONG ONT OPEN OPG OPN OP ORCA ORDI OSMO PARTI PENDLE PENGU PEOPLE PEPE PHA PLUME PNUT POL POLYX POWR PROM PROVE PSG PUMP PUNDIX PYTH RAD RARE RAY RE RED RENDER REQ REZ RIF RLC ROBO RONIN ROSE RPL RSR RUNE RVN SAGA SAHARA SAND SANTOS SAPIEN SCR SC SEI SENT SFP SHELL SHIB SIGN SKL SKY SLP SNX SOL SOLV SOMI SOPH SPELL SPK SSV STEEM STG STRAX STRK STX SUI SUN SUPER S SUSHI SXT SYRUP TAO TFUEL THETA THE TIA TKO TNSR TON TOWNS TRB TREE TRUMP TRX TST TURBO TURTLE T TUT TWT UMA UNI USUAL U VANA VET VIRTUAL VTHO WAL WAXP WCT WIN WLD WLFI WOO W XAI XEC XLM XNO XPL XRP XTZ XVG XVS YB YFI YGG ZAMA ZBT ZEC ZEN ZIL ZKC ZKP ZK ZRO ZRX
"""

SYMBOLS = [s.strip() + "USDT" for s in RAW_SYMBOLS.split() if s.strip()]

session = requests.Session()
adapter = HTTPAdapter(
    pool_connections=50,
    pool_maxsize=50,
    max_retries=Retry(total=2, backoff_factor=0.1)
)
session.mount("https://", adapter)

MOROCCO_TZ = timezone(timedelta(hours=1))

def load_json(path):
    if os.path.exists(path):
        try:
            with open(path, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_json(path, data):
    with open(path, 'w') as f:
        json.dump(data, f)

def send_telegram_alert(msg: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": msg}
    try:
        session.post(url, json=payload, timeout=10)
        print("[OK] Alert sent to Telegram")
    except Exception as e:
        print(f"[ERROR] Telegram error: {e}")

def get_binance_server_time() -> int:
    response = session.get("https://api.binance.com/api/v3/time")
    response.raise_for_status()
    return response.json()["serverTime"]

def seconds_until_next_15min() -> float:
    server_time_ms = get_binance_server_time()
    now = datetime.datetime.fromtimestamp(server_time_ms / 1000, tz=timezone.utc)
    next_minute = ((now.minute // 15) + 1) * 15
    if next_minute >= 60:
        target = now.replace(minute=0, second=2, microsecond=0) + datetime.timedelta(hours=1)
    else:
        target = now.replace(minute=next_minute, second=2, microsecond=0)
    return (target - now).total_seconds()

def calculate_rsi_wilders(closes, period=14):
    if len(closes) < period + 1:
        return None
    
    gains = []
    losses = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        
    if avg_loss == 0:
        return 100.0
    
    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi

def get_24hr_tickers():
    try:
        resp = session.get("https://api.binance.com/api/v3/ticker/24hr", timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return {item['symbol']: float(item['quoteVolume']) for item in data}
    except Exception as e:
        print(f"[WARN] Error fetching 24hr tickers: {e}")
        return {}

def scan_symbol(sym: str):
    try:
        resp = session.get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": sym, "interval": "15m", "limit": 200},
            timeout=5
        )
        resp.raise_for_status()
        data = resp.json()
        if len(data) < 16:
            return None

        closed = data[:-1]
        volumes = [float(c[5]) for c in closed]
        closes = [float(c[4]) for c in closed]

        count = 1
        for i in range(len(volumes) - 1, 0, -1):
            if volumes[i] > volumes[i - 1]:
                count += 1
            else:
                break

        if count < 3:
            return None

        last = closed[-1]
        prev = closed[-2]

        vol_ratio = volumes[-1] / volumes[-2] if volumes[-2] > 0 else 0
        streak_ratio = volumes[-1] / volumes[-count] if volumes[-count] > 0 else 0

        close_p = float(last[4])
        prev_close = float(prev[4])

        pc = ((close_p - prev_close) / prev_close) * 100

        if pc < PC_ALERT:
            return None

        rsi = calculate_rsi_wilders(closes, RSI_PERIOD)
        
        if rsi is None or not (RSI_MIN <= rsi <= RSI_MAX):
            return None

        green_count = sum(1 for c in closed[-count:] if float(c[4]) >= float(c[1]))

        return {
            "sym": sym.replace("USDT", ""),
            "count": count,
            "close": close_p,
            "pc": pc,
            "vol_ratio": vol_ratio,
            "streak_ratio": streak_ratio,
            "green_count": green_count,
            "rsi": rsi,
        }
    except Exception:
        return None

def main():
    print("[START] Rising Volume Scanner (15min) initialized...")
    alerted = load_json(ALERTED_FILE)
    prev_scan_syms = set()

    while True:
        try:
            secs = seconds_until_next_15min()
            now_str = datetime.datetime.now(MOROCCO_TZ).strftime('%H:%M:%S')
            print(f"[{now_str}] Sleeping {secs:.0f}s until next 15min candle...")
            time.sleep(secs)

            now_ts = time.time()
            time_str = datetime.datetime.now(MOROCCO_TZ).strftime('%H:%M')
            t0 = time.time()
            print(f"\n[SCAN] Scanning {len(SYMBOLS)} coins...")

            tickers_24h = get_24hr_tickers()
            results = []

            with ThreadPoolExecutor(max_workers=50) as executor:
                futures = {executor.submit(scan_symbol, sym): sym for sym in SYMBOLS}
                for future in as_completed(futures):
                    result = future.result()
                    if result:
                        sym = result["sym"]
                        full_sym = sym + "USDT"
                        
                        last_alerted_count = alerted.get(sym, {}).get("count", 0)
                        count = result["count"]
                        
                        if count > last_alerted_count:
                            if sym in prev_scan_syms:
                                alerted[sym] = {"count": count, "ts": now_ts}
                                continue
                            
                            day_pc = None
                            try:
                                day_resp = session.get(
                                    "https://api.binance.com/api/v3/klines",
                                    params={"symbol": full_sym, "interval": "1d", "limit": 2},
                                    timeout=5
                                )
                                day_resp.raise_for_status()
                                day_data = day_resp.json()
                                prev_day_close = float(day_data[-2][4])
                                day_pc = ((result['close'] - prev_day_close) / prev_day_close) * 100
                            except Exception:
                                pass

                            # NEW FILTERS
                            # Day PC filter: between -8% and +6%
                            if day_pc is not None and not (MIN_DAY_PC <= day_pc <= MAX_DAY_PC):
                                continue

                            # Vol ratio filter: between 1.4 and 8.2
                            if not (MIN_VOL_RATIO <= result['vol_ratio'] <= MAX_VOL_RATIO):
                                continue

                            # Streak ratio filter: less than 25
                            if result['streak_ratio'] > MAX_STREAK_RATIO:
                                continue

                            vol_24h = tickers_24h.get(full_sym, 0.0)
                            
                            result["vol_24h"] = vol_24h
                            result["day_pc"] = day_pc
                            
                            results.append(result)
                            alerted[sym] = {"count": count, "ts": now_ts}

            print(f"[DONE] Scan completed in {time.time() - t0:.1f}s")
            prev_scan_syms = {r["sym"] for r in results}

            if results:
                results.sort(key=lambda x: x["vol_24h"], reverse=True)
                
                lines = [f"BUY SIGNAL - {time_str} (GMT+1)\n"]
                for c in results:
                    pc_str = f"{c['pc']:+.2f}%"
                    day_str = f"{c['day_pc']:+.2f}%" if c["day_pc"] is not None else "N/A"
                    
                    lines.append(
                        f"🚀 {c['sym']} 💰{c['close']} 📈{pc_str} x{c['vol_ratio']:.1f}\n"
                        f"📅{day_str} greens: {c['green_count']}/{c['count']} streak: x{c['streak_ratio']:.1f}"
                    )
                    lines.append("")

                send_telegram_alert("\n".join(lines))
                print("\n".join(lines))

            save_json(ALERTED_FILE, alerted)

        except KeyboardInterrupt:
            print("\n[STOP] Scanner stopped by user.")
            break
        except Exception as e:
            print(f"[ERROR] Main loop error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
