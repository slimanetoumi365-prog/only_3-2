import requests
import time
import datetime
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# --- Configuration ---
RAW_SYMBOLS = """
0G 1000CAT 1000CHEEMS 1000SATS 1INCH 1MBABYDOGE 2Z AAVE ACE ACH ACM ACT ACX ADA ADX AEVO AGLD AIGENSYN AI AIXBT ALGO ALICE ALLO ALPINE ALT AMP ANIME ANKR APE API3 APT ARB ARKM ARK ARPA AR ASR ASTER ASTR ATM ATOM AT AUCTION AUDIO A AVA AVAX AVNT AWE AXL AXS BABY BANANAS31 BANANA BAND BANK BARD BAR BAT BB BCH BEAMX BEL BERA BICO BIGTIME BIO BLUR BMT BNB BNSOL BNT BOME BONK BREV BROCCOLI714 C98 CAKE CATI CELO CELR CETUS CFG CFX CGPT CHIP CHR CHZ CITY CKB COMP COOKIE COTI COW CRV CTK CTSI C CVC CVX CYBER DASH DCR DEXE DGB DIA DODO DOGE DOGS DOLO DOT DUSK DYDX DYM EDEN EDU EGLD EIGEN ENA ENJ ENSO ENS EPIC ERA ESP ETC EUL FET FF FIDA FIL FLOKI FLOW FLUX FOGO FORM FRAX FTT F GALA GAS GENIUS GIGGLE GLMR GLM GMT GMX GNO GNS GPS GRT GTC GUN G HAEDAL HBAR HEI HEMI HFT HIVE HMSTR HOLO HOME HOT HUMA HYPER ICP ICX ID ILV IMX INIT INJ IOST IOTA IOTX IO IQ JASMY JOE JST JTO JUP JUV KAIA KAITO KAT KAVA KERNEL KGST KITE KMNO KNC KSM LA LAYER LAZIO LDO LINEA LINK LISTA LPT LQTY LSK LTC LUMIA LUNA LUNC MAGIC MANA MANTA MANTRA MASK MAV MBL MEGA MEME METIS MET ME MINA MIRA MITO MMT MORPHO MOVE MOVR MTL MUBARAK NEAR NEIRO NEO NEWT NEXO NIGHT NIL NMR NOM NOT NXPC OGN OG ONDO ONE ONG ONT OPEN OPG OPN OP ORCA ORDI OSMO PARTI PENDLE PENGU PEOPLE PEPE PHA PIVX PIXEL PLUME PNUT POL POLYX PORTAL PORTO POWR PROM PROVE PSG PUMP PUNDIX PYR PYTH QI QKC QNT QTUM QUICK RAD RARE RAY RE RED RENDER REQ RESOLV REZ RIF RLC ROBO RONIN ROSE RPL RSR RUNE RVN SAGA SAHARA SAND SANTOS SAPIEN SCRT SCR SC SEI SENT SFP SHELL SHIB SIGN SKL SKY SLP SNX SOL SOLV SOMI SOPH SPELL SPK SSV STEEM STG STORJ STO STRAX STRK STX SUI SUN SUPER S SUSHI SXT SYN SYRUP TAO TFUEL THETA THE TIA TKO TLM TNSR TON TOWNS TRB TREE TRUMP TRX TST TURBO TURTLE T TUT TWT UMA UNI USUAL U VANA VANRY VELODROME VET VIC VIRTUAL VTHO WAL WAXP WCT WIF WIN WLD WLFI WOO W XAI XEC XLM XNO XPL XRP XTZ XVG XVS YB YFI YGG ZAMA ZBT ZEC ZEN ZIL ZKC ZKP ZK ZRO ZRX
"""

TELEGRAM_BOT_TOKEN = '7913078821:AAH_jUTHXlFx66daqBkYY7mKw7UZnwpp_A0'
TELEGRAM_CHAT_ID = '1692583809'

ALERTED_FILE = "alerted_coins.json"

PC_ALERT = 1.9

SYMBOLS = [s.strip() + "USDT" for s in RAW_SYMBOLS.split() if s.strip()]

session = requests.Session()
adapter = HTTPAdapter(
    pool_connections=100,
    pool_maxsize=100,
    max_retries=Retry(total=2, backoff_factor=0.1)
)
session.mount("https://", adapter)


def load_json(path):
    if os.path.exists(path):
        try:
            with open(path, 'r') as f:
                return json.load(f)
        except:
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
        print(f"✅ Alert sent")
    except Exception as e:
        print(f"❌ Telegram error: {e}")


def get_binance_server_time() -> int:
    response = session.get("https://api.binance.com/api/v3/time")
    response.raise_for_status()
    return response.json()["serverTime"]


def seconds_until_next_15min() -> float:
    server_time_ms = get_binance_server_time()
    now = datetime.datetime.fromtimestamp(server_time_ms / 1000, tz=datetime.timezone.utc)
    next_minute = ((now.minute // 15) + 1) * 15
    if next_minute >= 60:
        target = now.replace(minute=0, second=2, microsecond=0) + datetime.timedelta(hours=1)
    else:
        target = now.replace(minute=next_minute, second=2, microsecond=0)
    return (target - now).total_seconds()


def count_rising_volumes(sym: str):
    try:
        resp = session.get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": sym, "interval": "15m", "limit": 10},
            timeout=5
        )
        resp.raise_for_status()
        data = resp.json()
        if len(data) < 5:
            return None

        closed = data[:-1]
        volumes = [float(c[5]) for c in closed]

        count = 1
        for i in range(len(volumes) - 1, 0, -1):
            if volumes[i] > volumes[i - 1]:
                count += 1
            else:
                break

        if count < 4:
            return None

        streak = closed[-count:]
        last = closed[-1]
        prev = closed[-2]

        vol_ratio = volumes[-1] / volumes[-2] if volumes[-2] > 0 else 0
        streak_ratio = volumes[-1] / volumes[-count] if volumes[-count] > 0 else 0

        close_p = float(last[4])
        open_p = float(last[1])
        high_p = float(last[2])
        low_p = float(last[3])
        prev_close = float(prev[4])  # previous candle close

        # % change from previous candle close (matches Binance chart)
        pc = ((close_p - prev_close) / prev_close) * 100

        if pc < PC_ALERT:
            return None

        # Body % of full range (still uses candle open/close)
        full_range = high_p - low_p
        body = abs(close_p - open_p)
        body_pct = (body / full_range * 100) if full_range > 0 else 0

        # Previous candle color
        prev_open = float(prev[1])
        prev_color = "🟢" if float(prev[4]) >= prev_open else "🔴"

        # Green candles in streak
        green_count = sum(1 for c in streak if float(c[4]) >= float(c[1]))

        # 24h ticker
        day_pc = None
        high_24h = None
        low_24h = None
        vol_24h_usdt = None
        vs_avg = None

        try:
            ticker_resp = session.get(
                "https://api.binance.com/api/v3/ticker/24hr",
                params={"symbol": sym},
                timeout=5
            )
            ticker_resp.raise_for_status()
            ticker = ticker_resp.json()
            high_24h = float(ticker["highPrice"])
            low_24h = float(ticker["lowPrice"])
            vol_24h_usdt = float(ticker["quoteVolume"])
            open_24h = float(ticker["openPrice"])
            day_pc = ((close_p - open_24h) / open_24h) * 100
            avg_15m_vol_usdt = vol_24h_usdt / 96
            current_15m_vol_usdt = float(last[5]) * close_p
            vs_avg = current_15m_vol_usdt / avg_15m_vol_usdt if avg_15m_vol_usdt > 0 else 0
        except:
            pass

        dist_high = ((close_p - high_24h) / high_24h * 100) if high_24h else None
        dist_low = ((close_p - low_24h) / low_24h * 100) if low_24h else None

        return {
            "sym": sym.replace("USDT", ""),
            "count": count,
            "close": close_p,
            "pc": pc,
            "day_pc": day_pc,
            "vol_ratio": vol_ratio,
            "streak_ratio": streak_ratio,
            "body_pct": body_pct,
            "prev_color": prev_color,
            "green_count": green_count,
            "vol_24h_usdt": vol_24h_usdt,
            "vs_avg": vs_avg,
            "dist_high": dist_high,
            "dist_low": dist_low,
        }

    except Exception as e:
        print(f"⚠️ Error {sym}: {e}")
        return None


def fmt_vol(v):
    if v is None:
        return "N/A"
    if v >= 1_000_000:
        return f"${v/1_000_000:.1f}M"
    if v >= 1_000:
        return f"${v/1_000:.0f}K"
    return f"${v:.0f}"


def main():
    print("🚀 Starting Rising Volume Scanner (15min)...")
    alerted = load_json(ALERTED_FILE)
    prev_scan_syms = set()

    while True:
        try:
            secs = seconds_until_next_15min()
            now = datetime.datetime.utcnow().strftime('%H:%M:%S')
            print(f"[{now}] Sleeping {secs:.0f}s until next 15min candle...")
            time.sleep(secs)

            now_ts = time.time()
            time_str = datetime.datetime.utcnow().strftime('%H:%M')
            t0 = time.time()
            print(f"\n🔍 Scanning {len(SYMBOLS)} coins...")

            results = []

            with ThreadPoolExecutor(max_workers=100) as executor:
                futures = {executor.submit(count_rising_volumes, sym): sym for sym in SYMBOLS}
                for future in as_completed(futures):
                    result = future.result()
                    if result:
                        sym = result["sym"]
                        count = result["count"]

                        last_alerted_count = alerted.get(sym, {}).get("count", 0)
                        last_alerted_ts = alerted.get(sym, {}).get("ts", 0)

                        if now_ts - last_alerted_ts > 2 * 3600:
                            last_alerted_count = 0

                        if count > last_alerted_count:
                            if sym in prev_scan_syms:
                                print(f"⏭ Skipping {sym} — consecutive scan")
                                alerted[sym] = {"count": count, "ts": now_ts}
                                continue
                            results.append(result)
                            alerted[sym] = {"count": count, "ts": now_ts}

            print(f"⚡ Scan done in {time.time() - t0:.1f}s")

            prev_scan_syms = {r["sym"] for r in results}

            if results:
                results.sort(key=lambda x: x["vol_ratio"], reverse=True)
                lines = [f"🔥 Rising Volume — {time_str} UTC\n"]
                for c in results:
                    pc_str = f"{c['pc']:+.2f}%"
                    day_str = f"{c['day_pc']:+.2f}%" if c["day_pc"] is not None else "N/A"
                    dist_high_str = f"{c['dist_high']:+.1f}%" if c["dist_high"] is not None else "N/A"
                    dist_low_str = f"+{c['dist_low']:.1f}%" if c["dist_low"] is not None else "N/A"
                    vs_avg_str = f"x{c['vs_avg']:.1f}" if c["vs_avg"] is not None else "N/A"

                    lines.append(
                        f"🚀 {c['sym']} 💰{c['close']} 📈{pc_str} 📅{day_str} x{c['vol_ratio']:.1f} [{c['count']}]\n"
                        f"🕯 prev: {c['prev_color']} | body: {c['body_pct']:.0f}% | greens: {c['green_count']}/{c['count']} | streak: x{c['streak_ratio']:.1f}\n"
                        f"📊 vol24h: {fmt_vol(c['vol_24h_usdt'])} | vs avg: {vs_avg_str}\n"
                        f"📉 {dist_high_str} from 24h high | {dist_low_str} from 24h low"
                    )
                    lines.append("")

                send_telegram_alert("\n".join(lines))
                print("\n".join(lines))

            save_json(ALERTED_FILE, alerted)

        except KeyboardInterrupt:
            print("\n🛑 Scanner stopped.")
            break
        except Exception as e:
            print(f"💥 Main loop error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
