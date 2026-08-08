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

TRACKED_FILE = "tracked_coins.json"

# --- SCAN REQUIREMENTS ---
MIN_CANDLE_PC = 2.0  # Minimum 1h candle % change to trigger
TRACK_HOURS = 4      # 8 hours: Perfect for catching a 1-2 hour consolidation (bull flag) before the next leg up

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
        response = session.post(url, json=payload, timeout=10)
        response_data = response.json()
        if response_data.get("ok"):
            print("[OK] Alert successfully delivered to Telegram")
        else:
            print(f"[ERROR] Telegram rejected the message: {response_data}")
    except Exception as e:
        print(f"[ERROR] Telegram network error: {e}")

def get_binance_server_time() -> int:
    response = session.get("https://api.binance.com/api/v3/time")
    response.raise_for_status()
    return response.json()["serverTime"]

def seconds_until_next_1h() -> float:
    server_time_ms = get_binance_server_time()
    now = datetime.datetime.fromtimestamp(server_time_ms / 1000, tz=timezone.utc)
    target = now.replace(minute=0, second=1, microsecond=0) + datetime.timedelta(hours=1)
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

def scan_symbol(sym: str):
    try:
        # Fetch 200 candles for accurate RSI(14) calculation (matches Binance exactly)
        resp = session.get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": sym, "interval": "1h", "limit": 200},
            timeout=5
        )
        resp.raise_for_status()
        data = resp.json()
        if len(data) < 16:
            return None

        # EXCLUDE currently forming candle - use only CLOSED candles
        closed = data[:-1]
        
        last = closed[-1]
        prev = closed[-2]
        
        close_p = float(last[4])
        prev_close = float(prev[4])
        volume = float(last[5])
        prev_volume = float(prev[5])
        
        # Calculate percentage using CLOSED candle closes (Binance method)
        pc = ((close_p - prev_close) / prev_close) * 100
        
        if pc < MIN_CANDLE_PC:
            return None
        
        # Calculate RSI using all 199 closed candles
        closes = [float(c[4]) for c in closed]
        rsi = calculate_rsi_wilders(closes, 14)
        
        # Calculate 24h change (from 24 closed 1h candles ago)
        if len(closed) >= 25:
            prev_24h_close = float(closed[-25][4])
            change_24h = ((close_p - prev_24h_close) / prev_24h_close) * 100 if prev_24h_close > 0 else 0
        else:
            change_24h = 0.0
        
        # Fetch yesterday's daily close (last fully closed daily candle)
        from_yesterday_pc = 0.0
        try:
            day_resp = session.get(
                "https://api.binance.com/api/v3/klines",
                params={"symbol": sym, "interval": "1d", "limit": 2},
                timeout=5
            )
            day_resp.raise_for_status()
            day_data = day_resp.json()
            if len(day_data) >= 2:
                yesterday_close = float(day_data[-2][4])  # Last fully closed daily candle
                if yesterday_close > 0:
                    from_yesterday_pc = ((close_p - yesterday_close) / yesterday_close) * 100
        except Exception:
            pass
            
        vol_ratio = volume / prev_volume if prev_volume > 0 else 1.0
        
        return {
            "sym": sym.replace("USDT", ""),
            "full_sym": sym,
            "close": close_p,
            "prev_close": prev_close,
            "pc": pc,
            "vol_ratio": vol_ratio,
            "change_24h": change_24h,
            "from_yesterday_pc": from_yesterday_pc,
            "rsi": rsi,
            "timestamp": time.time()
        }
    except Exception:
        return None

def main():
    print("[START] 1-Hour Momentum Tracker (8h Window, Rolling Tracking) initialized...")
    tracked = load_json(TRACKED_FILE)
    
    now_ts = time.time()
    # Clean up expired tracked coins
    tracked = {sym: data for sym, data in tracked.items() if data.get("tracked_until", 0) > now_ts}
    save_json(TRACKED_FILE, tracked)

    while True:
        try:
            secs = seconds_until_next_1h()
            now_str = datetime.datetime.now(MOROCCO_TZ).strftime('%H:%M:%S')
            print(f"[{now_str}] Sleeping {secs:.0f}s until next 1h candle closes...")
            time.sleep(secs)

            now_ts = time.time()
            time_str = datetime.datetime.now(MOROCCO_TZ).strftime('%Y-%m-%d %H:%M')
            t0 = time.time()
            print(f"\n[SCAN] Scanning {len(SYMBOLS)} coins for >= {MIN_CANDLE_PC}% green 1h candles...")

            results = []
            with ThreadPoolExecutor(max_workers=50) as executor:
                futures = {executor.submit(scan_symbol, sym): sym for sym in SYMBOLS}
                for future in as_completed(futures):
                    result = future.result()
                    if result:
                        results.append(result)

            print(f"[DONE] Found {len(results)} coins meeting criteria in {time.time() - t0:.1f}s")

            # Clean up expired tracking dynamically
            tracked = {sym: data for sym, data in tracked.items() if data.get("tracked_until", 0) > now_ts}

            alerts_to_send = []

            for res in results:
                sym = res["sym"]
                
                if sym in tracked:
                    # This is the 2nd (or 3rd, 4th...) +2% candle
                    # Send alert
                    first_prev_close = tracked[sym]["first_prev_close"]
                    first_pc = tracked[sym]["first_pc"]
                    first_ts = tracked[sym]["first_trigger_ts"]
                    
                    total_pc = ((res["close"] - first_prev_close) / first_prev_close) * 100
                    hours_elapsed = int((now_ts - first_ts) / 3600)
                    
                    res["alert_count"] = tracked[sym]["alert_count"] + 1
                    res["total_pc"] = total_pc
                    res["first_pc"] = first_pc
                    res["hours_elapsed"] = hours_elapsed
                    
                    alerts_to_send.append(res)
                    
                    # RESET tracking: make this candle the new "first"
                    tracked[sym] = {
                        "tracked_until": now_ts + (TRACK_HOURS * 3600),
                        "alert_count": 1,
                        "first_prev_close": res["prev_close"],
                        "first_pc": res["pc"],
                        "first_trigger_ts": now_ts
                    }
                    print(f"[INFO] {sym} triggered {res['alert_count']}th >= {MIN_CANDLE_PC}% candle. Alert sent. Tracking reset for next {TRACK_HOURS}h.")
                else:
                    # First trigger: Start tracking, NO alert sent
                    tracked[sym] = {
                        "tracked_until": now_ts + (TRACK_HOURS * 3600),
                        "alert_count": 1,
                        "first_prev_close": res["prev_close"],
                        "first_pc": res["pc"],
                        "first_trigger_ts": now_ts
                    }
                    print(f"[INFO] {sym} triggered 1st >= {MIN_CANDLE_PC}% candle. Tracking started for {TRACK_HOURS}h.")

            if alerts_to_send:
                # Sort by Vol Ratio (highest to lowest)
                alerts_to_send.sort(key=lambda x: x["vol_ratio"], reverse=True)
                
                lines = []
                
                for c in alerts_to_send:
                    lines.append(f"{time_str}")
                    lines.append(f"{c['sym']} {c['close']}")
                    lines.append(f"Total Gain: {c['total_pc']:+.2f}% | 24h: {c['change_24h']:+.2f}% | From Yesterday: {c['from_yesterday_pc']:+.2f}%")
                    lines.append(f"1st Candle: {c['first_pc']:+.2f}% | Current: {c['pc']:+.2f}%")
                    lines.append(f"Time elapsed: {c['hours_elapsed']}h")
                    lines.append(f"RSI: {c['rsi']:.1f} | Vol Ratio: {c['vol_ratio']:.2f}x")
                    lines.append("")

                send_telegram_alert("\n".join(lines))
                print("\n--- ALERTS SENT TO TELEGRAM ---")
                print("\n".join(lines))
            
            save_json(TRACKED_FILE, tracked)

        except KeyboardInterrupt:
            print("\n[STOP] Scanner stopped by user.")
            break
        except Exception as e:
            print(f"[ERROR] Main loop error: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()
