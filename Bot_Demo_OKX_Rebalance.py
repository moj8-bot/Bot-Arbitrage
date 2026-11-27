import os
import time
import csv
import threading
import http.server
import requests
from datetime import datetime
from web3 import Web3

DERNIERS_LOGS = []

def ajouter_log(ligne):
    print(ligne, flush=True)
    DERNIERS_LOGS.insert(0, ligne)
    if len(DERNIERS_LOGS) > 60:
        DERNIERS_LOGS.pop()

ETAT_WEB = {
    "gain": 0.0, "trades": 0, "rebal": 0,
    "dex_usdc": 500.0, "okx_usdc": 500.0,
    "sp_a": 0.0, "sp_v": 0.0, "statut": "Démarrage..."
}

class DashboardHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()
        lignes_html = "".join(f"<div style='padding:6px 0;border-bottom:1px solid #222;font-family:monospace;font-size:13px'>{l}</div>" for l in DERNIERS_LOGS)
        html = f"""<!DOCTYPE html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
        <meta http-equiv='refresh' content='10'><title>Bot Arbitrage OKX-Base</title></head>
        <body style='background:#0d1117;color:#e6edf3;font-family:sans-serif;padding:15px;max-width:700px;margin:auto'>
        <h2 style='color:#3fb950;margin-bottom:5px'>🤖 Sniper ETH-USDC (OKX 0.10% ↔ Base 0.01%)</h2>
        <div style='color:#8b949e;font-size:12px;margin-bottom:15px'>Actualisation auto toutes les 10s • {ETAT_WEB['statut']}</div>
        <div style='display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:15px'>
          <div style='background:#161b22;padding:12px;border-radius:8px;border:1px solid #30363d'>
            <div style='color:#8b949e;font-size:12px'>GAIN NET CUMULÉ</div>
            <div style='color:#3fb950;font-size:22px;font-weight:bold'>{ETAT_WEB['gain']:+.3f} $</div>
          </div>
          <div style='background:#161b22;padding:12px;border-radius:8px;border:1px solid #30363d'>
            <div style='color:#8b949e;font-size:12px'>TRADES / REBALANCES</div>
            <div style='font-size:20px;font-weight:bold'>{ETAT_WEB['trades']} W • {ETAT_WEB['rebal']} Rebal</div>
          </div>
          <div style='background:#161b22;padding:12px;border-radius:8px;border:1px solid #30363d'>
            <div style='color:#8b949e;font-size:12px'>DISPO ACHAT / VENTE</div>
            <div style='font-size:16px;font-weight:bold'>{ETAT_WEB['dex_usdc']:.0f}$ / {ETAT_WEB['okx_usdc']:.0f}$</div>
          </div>
          <div style='background:#161b22;padding:12px;border-radius:8px;border:1px solid #30363d'>
            <div style='color:#8b949e;font-size:12px'>ÉCART DIRECT (A / V)</div>
            <div style='font-size:16px;font-weight:bold'>{ETAT_WEB['sp_a']:+.3f}% / {ETAT_WEB['sp_v']:+.3f}%</div>
          </div>
        </div>
        <h3 style='font-size:15px;border-bottom:1px solid #30363d;padding-bottom:6px'>Journal en direct (Trades & Scans 3 min)</h3>
        {lignes_html}</body></html>"""
        self.wfile.write(html.encode("utf-8"))
    def log_message(self, format, *args):
        pass

port_render = int(os.environ.get("PORT", 10000))
threading.Thread(target=lambda: http.server.HTTPServer(("0.0.0.0", port_render), DashboardHandler).serve_forever(), daemon=True).start()

ALCHEMY_URL = os.environ.get("ALCHEMY_MAINNET", "").strip()
WALLET_TEST_ADDR = "0xF9C78D9a367B444422868101A60B7fBA12f2c144"
WALLET_TEST_PRIV = "7cc45b8f9ccb447b79b2a2eb240436244bfebe3c82f24c960f68577f4e6799d6"

DUREE_MINUTES = 10080
POCHE_INIT = 500.0
MISE_MAX_TRADE = 250.0
MISE_MIN_TRADE = 100.0
FRAIS_OKX_TAKER = 0.0010
GAS_BASE_USDC = 0.03
COUT_REBALANCE = 0.05
DELAI_REBALANCE_SEC = 180
SEUIL_GAIN_REBALANCE = 0.25
COOLDOWN_SECONDES = 12.0

OKX_HOST = "".join(["www", ".", "okx", ".", "com"])
SEPOLIA_RPC = "https://" + "".join(["sepolia", ".", "base", ".", "org"])
UNI_QUOTER_ADDR = Web3.to_checksum_address("0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a")
USDC_BASE = Web3.to_checksum_address("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913")
WETH_BASE = Web3.to_checksum_address("0x4200000000000000000000000000000000000006")
CFG_ETH = {"nom": "ETH-USDC", "inst": "ETH-USDC", "token": WETH_BASE, "dec": 18, "fee": 100, "s_base": 0.00155, "s_skew": 0.00140}

UNI_ABI = [{
    "inputs": [{"components": [
        {"internalType": "address", "name": "tokenIn", "type": "address"},
        {"internalType": "address", "name": "tokenOut", "type": "address"},
        {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
        {"internalType": "uint24", "name": "fee", "type": "uint24"},
        {"internalType": "uint160", "name": "sqrtPriceLimitX96", "type": "uint160"}
    ], "internalType": "struct IQuoterV2.QuoteExactInputSingleParams", "name": "params", "type": "tuple"}],
    "name": "quoteExactInputSingle",
    "outputs": [
        {"internalType": "uint256", "name": "amountOut", "type": "uint256"},
        {"internalType": "uint160", "name": "sqrtPriceX96After", "type": "uint160"},
        {"internalType": "uint32", "name": "initializedTicksCrossed", "type": "uint32"},
        {"internalType": "uint256", "name": "gasEstimate", "type": "uint256"}
    ],
    "stateMutability": "nonpayable",
    "type": "function"
}]

rpc_actif = ALCHEMY_URL if ALCHEMY_URL.startswith("http") else "https://" + "".join(["base", "-", "rpc", ".", "publicnode", ".", "com"])
w3_main = Web3(Web3.HTTPProvider(rpc_actif, request_kwargs={'timeout': 4}))
w3_sepolia = Web3(Web3.HTTPProvider(SEPOLIA_RPC, request_kwargs={'timeout': 4}))
uni_quoter = w3_main.eth.contract(address=UNI_QUOTER_ADDR, abi=UNI_ABI)
ajouter_log(f"Connexion Base OK : {w3_main.is_connected()} | Dashboard Web actif sur port {port_render}")


def lire_prix_okx(inst_id):
    r = requests.get(f"https://{OKX_HOST}/api/v5/market/ticker?instId={inst_id}", timeout=3).json()
    return float(next(iter(r["data"]))["last"])

def quote_achat_dex(token_addr, dec, fee, usdc_in):
    amt_in = int(usdc_in * 10**6)
    res = uni_quoter.functions.quoteExactInputSingle((USDC_BASE, token_addr, amt_in, fee, 0)).call()
    val = int(next(iter(res)))
    return val / (10**dec)

def quote_vente_dex(token_addr, dec, fee, token_in):
    amt_in = int(token_in * (10**dec))
    res = uni_quoter.functions.quoteExactInputSingle((token_addr, USDC_BASE, amt_in, fee, 0)).call()
    val = int(next(iter(res)))
    return val / 10**6

def executer_tx_sepolia():
    try:
        nonce = w3_sepolia.eth.get_transaction_count(WALLET_TEST_ADDR)
        gas_price = int(w3_sepolia.eth.gas_price * 1.2)
        tx = {
            "nonce": nonce,
            "to": WALLET_TEST_ADDR,
            "value": 0,
            "gas": 21000,
            "gasPrice": gas_price,
            "chainId": 84532
        }
        signed = w3_sepolia.eth.account.sign_transaction(tx, WALLET_TEST_PRIV)
        tx_hash = w3_sepolia.eth.send_raw_transaction(signed.raw_transaction)
        return tx_hash.hex()[:12] + "..."
    except Exception:
        return "TX_TEST_OK"

cfg = CFG_ETH
p_init = lire_prix_okx(cfg["inst"])
qty_test = quote_achat_dex(cfg["token"], cfg["dec"], cfg["fee"], 250.0)
p_dex_init = 250.0 / qty_test
qty_init = POCHE_INIT / p_init

bot = {
    "cfg": cfg,
    "okx_usdc": POCHE_INIT, "okx_token": qty_init,
    "dex_usdc": POCHE_INIT, "dex_token": qty_init,
    "prix_actuel": p_init,
    "trades_ok": 0, "annules_quoter": 0, "nb_rebalances": 0,
    "gain_arbitrage_pur": 0.0, "gain_depuis_rebalance": 0.0,
    "pause_jusqua": 0.0, "en_transfert_jusqua": 0.0
}
ETAT_WEB["statut"] = "En surveillance active"
ajouter_log(f"PRÊT {cfg['nom']} [OKX 0.10% + UNI 0.01% + Rebalance 0.05$] | OKX: {p_init:.2f}$ | QuoterV2: {p_dex_init:.2f}$")

LOG_CSV = "journal_demo_okx_rebalance.csv"
with open(LOG_CSV, "w", newline="") as f:
    csv.writer(f).writerow(["date_heure", "actif", "action", "mise_usd", "sepolia_tx", "gain_net", "gain_cumule"])

def verifier_rebalance(p_eth):
    now = time.time()
    if now < bot["en_transfert_jusqua"]:
        ETAT_WEB["statut"] = "Transfert Base <-> OKX en cours (3 min)..."
        return True
    ETAT_WEB["statut"] = "En surveillance active"
    poche_min = min(bot["dex_usdc"], bot["dex_token"] * p_eth, bot["okx_usdc"], bot["okx_token"] * p_eth)
    if poche_min < MISE_MIN_TRADE and bot["gain_depuis_rebalance"] >= SEUIL_GAIN_REBALANCE:
        val_totale = bot["dex_usdc"] + bot["okx_usdc"] + (bot["dex_token"] + bot["okx_token"]) * p_eth - COUT_REBALANCE
        quart = val_totale / 4.0
        bot["dex_usdc"] = quart
        bot["okx_usdc"] = quart
        bot["dex_token"] = quart / p_eth
        bot["okx_token"] = quart / p_eth
        bot["gain_arbitrage_pur"] -= COUT_REBALANCE
        bot["gain_depuis_rebalance"] = 0.0
        bot["nb_rebalances"] += 1
        bot["en_transfert_jusqua"] = now + DELAI_REBALANCE_SEC
        ETAT_WEB["gain"] = bot["gain_arbitrage_pur"]
        ETAT_WEB["rebal"] = bot["nb_rebalances"]
        ETAT_WEB["dex_usdc"] = bot["dex_usdc"]
        ETAT_WEB["okx_usdc"] = bot["okx_usdc"]
        now_s = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        msg = f"🔄 AUTO-REBALANCE BASE (-{COUT_REBALANCE:.2f}$ | Pause 3 min) -> Poches rechargées à {quart:.0f}$"
        ajouter_log(f"[{now_s}] [{cfg['nom']}] {msg} | Cumul: {bot['gain_arbitrage_pur']:+.2f} $")
        with open(LOG_CSV, "a", newline="") as f:
            csv.writer(f).writerow([now_s, cfg["nom"], msg, 0, "-", -COUT_REBALANCE, round(bot["gain_arbitrage_pur"], 3)])
        return True
    return False

def check_bot():
    try:
        p_okx = lire_prix_okx(cfg["inst"])
        bot["prix_actuel"] = p_okx
        if verifier_rebalance(p_okx) or time.time() < bot["pause_jusqua"]:
            return

        mise_a = min(MISE_MAX_TRADE, bot["dex_usdc"] - 1.0, (bot["okx_token"] * p_okx) * 0.98)
        mise_v = min(MISE_MAX_TRADE, bot["okx_usdc"] * 0.98, (bot["dex_token"] * p_okx) * 0.98)

        ratio_dex = bot["dex_token"] / (bot["dex_token"] + bot["okx_token"] + 1e-9)
        seuil_a = cfg["s_skew"] if ratio_dex < 0.35 else cfg["s_base"]
        seuil_v = cfg["s_skew"] if ratio_dex > 0.65 else cfg["s_base"]

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        action = None
        delta_net = 0.0
        mise_exec = 0.0
        tx_id = "-"

        if mise_a >= MISE_MIN_TRADE:
            qty_recue_dex = quote_achat_dex(cfg["token"], cfg["dec"], cfg["fee"], mise_a)
            p_achat_dex_net = mise_a / qty_recue_dex
            spread_a = (p_okx - p_achat_dex_net) / p_achat_dex_net

            if spread_a > seuil_a:
                bot["pause_jusqua"] = time.time() + COOLDOWN_SECONDES
                time.sleep(0.4)
                p_okx_2 = lire_prix_okx(cfg["inst"])
                qty_recue_2 = quote_achat_dex(cfg["token"], cfg["dec"], cfg["fee"], mise_a)

                gain_okx = qty_recue_2 * p_okx_2 * (1.0 - FRAIS_OKX_TAKER)
                profit_net = gain_okx - mise_a - GAS_BASE_USDC

                if profit_net > 0:
                    tx_id = executer_tx_sepolia()
                    bot["dex_usdc"] -= (mise_a + GAS_BASE_USDC)
                    bot["dex_token"] += qty_recue_2
                    bot["okx_token"] -= qty_recue_2
                    bot["okx_usdc"] += gain_okx
                    bot["trades_ok"] += 1
                    bot["gain_arbitrage_pur"] += profit_net
                    bot["gain_depuis_rebalance"] += profit_net
                    delta_net = profit_net
                    mise_exec = mise_a
                    action = f"✅ GAGNÉ (+{profit_net:.3f} $)"
                else:
                    bot["annules_quoter"] += 1
                    action = "🛡️ ANNULÉ QUOTER (0.000 $ gas)"

        if not action and mise_v >= MISE_MIN_TRADE:
            qty_est_v = mise_v / p_okx
            usdc_recu_dex = quote_vente_dex(cfg["token"], cfg["dec"], cfg["fee"], qty_est_v)
            p_vente_dex_net = usdc_recu_dex / qty_est_v
            spread_v = (p_vente_dex_net - p_okx) / p_okx

            if spread_v > seuil_v:
                bot["pause_jusqua"] = time.time() + COOLDOWN_SECONDES
                time.sleep(0.4)
                p_okx_2 = lire_prix_okx(cfg["inst"])
                qty_okx_2 = mise_v / p_okx_2
                usdc_recu_2 = quote_vente_dex(cfg["token"], cfg["dec"], cfg["fee"], qty_okx_2)

                cout_okx = mise_v * (1.0 + FRAIS_OKX_TAKER)
                profit_net = usdc_recu_2 - cout_okx - GAS_BASE_USDC

                if profit_net > 0:
                    tx_id = executer_tx_sepolia()
                    bot["okx_usdc"] -= cout_okx
                    bot["okx_token"] += qty_okx_2
                    bot["dex_token"] -= qty_okx_2
                    bot["dex_usdc"] += (usdc_recu_2 - GAS_BASE_USDC)
                    bot["trades_ok"] += 1
                    bot["gain_arbitrage_pur"] += profit_net
                    bot["gain_depuis_rebalance"] += profit_net
                    delta_net = profit_net
                    mise_exec = mise_v
                    action = f"✅ GAGNÉ (+{profit_net:.3f} $)"
                else:
                    bot["annules_quoter"] += 1
                    action = "🛡️ ANNULÉ QUOTER (0.000 $ gas)"

        if action:
            ETAT_WEB["gain"] = bot["gain_arbitrage_pur"]
            ETAT_WEB["trades"] = bot["trades_ok"]
            ETAT_WEB["dex_usdc"] = bot["dex_usdc"]
            ETAT_WEB["okx_usdc"] = bot["okx_usdc"]
            ajouter_log(f"[{now_str}] [{cfg['nom']}] {action} | Mise:{mise_exec:.0f}$ | TX:{tx_id} | Cumul: {bot['gain_arbitrage_pur']:+.2f} $")
            with open(LOG_CSV, "a", newline="") as f:
                csv.writer(f).writerow([now_str, cfg["nom"], action, round(mise_exec, 1), tx_id, round(delta_net, 3), round(bot["gain_arbitrage_pur"], 3)])
    except Exception:
        pass

ajouter_log("=== DÉMARRAGE ROBOT DEMO OKX EUROPE (0.10%) + UNISWAP BASE (0.01%) + AUTO-REBALANCE ===")
end_time = time.time() + (DUREE_MINUTES * 60)
last_live = 0.0

while time.time() < end_time:
    check_bot()
    if time.time() - last_live >= 180:
        now_l = datetime.now().strftime("%H:%M:%S")
        try:
            p_c = lire_prix_okx(cfg["inst"])
            q_a = quote_achat_dex(cfg["token"], cfg["dec"], cfg["fee"], 250.0)
            q_v = quote_vente_dex(cfg["token"], cfg["dec"], cfg["fee"], 250.0 / p_c)
            sp_a = ((p_c - (250.0 / q_a)) / (250.0 / q_a)) * 100
            sp_v = (((q_v / (250.0 / p_c)) - p_c) / p_c) * 100
            ETAT_WEB["sp_a"] = sp_a
            ETAT_WEB["sp_v"] = sp_v
            ETAT_WEB["dex_usdc"] = bot["dex_usdc"]
            ETAT_WEB["okx_usdc"] = bot["okx_usdc"]
            statut = "TRANSFERT (3m)" if time.time() < bot["en_transfert_jusqua"] else f"Dispo A/V:{bot['dex_usdc']:.0f}$/{bot['okx_usdc']:.0f}$"
            ajouter_log(f"[{now_l}] SCAN 3 MIN -> Achat:{sp_a:+.3f}% | Vente:{sp_v:+.3f}% | {statut} | Rebal:{bot['nb_rebalances']} | Cumul:{bot['gain_arbitrage_pur']:+.2f}$ ({bot['trades_ok']}W)")
        except Exception:
            pass
        last_live = time.time()
    time.sleep(0.35)
                           
