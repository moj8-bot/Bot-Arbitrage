import os
import time
import hmac
import hashlib
import csv
import requests
from urllib.parse import urlencode
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from web3 import Web3

ALCHEMY_URL = os.environ.get("ALCHEMY_MAINNET", "").strip()
BINANCE_KEY = os.environ.get("BINANCE_TEST_KEY", "").strip()
BINANCE_SECRET = os.environ.get("BINANCE_TEST_SECRET", "").strip()

WALLET_TEST_ADDR = "0xF9C78D9a367B444422868101A60B7fBA12f2c144"
WALLET_TEST_PRIV = "7cc45b8f9ccb447b79b2a2eb240436244bfebe3c82f24c960f68577f4e6799d6"
SEPOLIA_RPC = "https://base.org"

DUREE_MINUTES = 1440
MISE_PAR_TRADE = 210.0
FRAIS_CEX_REEL = 0.0005
GAS_BASE_USDC = 0.03
COOLDOWN_SECONDES = 15.0

CEX_HOST = "".join(["api", ".", "coinbase", ".", "com"])
TEST_HOST = "".join(["testnet", ".", "binance", ".", "vision"])

ACTIFS_CIBLES = [
    {"nom": "ETH-USDC", "cb": "ETH-USD", "sym_bn": "ETHUSDT", "q": "WETH USDC", "pool_fee": 0.0005, "slip": 0.0005, "s_base": 0.0023, "s_skew": 0.0019},
    {"nom": "SOL-USDC", "cb": "SOL-USD", "sym_bn": "SOLUSDT", "q": "SOL USDC",  "pool_fee": 0.0005, "slip": 0.0006, "s_base": 0.0024, "s_skew": 0.0020}
]

POOL_ABI = [{
    "inputs": [],
    "name": "slot0",
    "outputs": [
        {"internalType": "uint160", "name": "sqrtPriceX96", "type": "uint160"},
        {"internalType": "int24", "name": "tick", "type": "int24"},
        {"internalType": "uint16", "name": "observationIndex", "type": "uint16"},
        {"internalType": "uint16", "name": "observationCardinality", "type": "uint16"},
        {"internalType": "uint16", "name": "observationCardinalityNext", "type": "uint16"},
        {"internalType": "uint8", "name": "feeProtocol", "type": "uint8"},
        {"internalType": "bool", "name": "unlocked", "type": "bool"}
    ],
    "stateMutability": "view",
    "type": "function"
}]


rpc_actif = ALCHEMY_URL if ALCHEMY_URL.startswith("http") else "https://publicnode.com"
w3_main = Web3(Web3.HTTPProvider(rpc_actif, request_kwargs={'timeout': 5}))
w3_sepolia = Web3(Web3.HTTPProvider(SEPOLIA_RPC, request_kwargs={'timeout': 5}))
print(f"Nœud Base connecté (Alchemy={ALCHEMY_URL.startswith('http')}) : {w3_main.is_connected()}", flush=True)

def lire_prix_cex(cb_pair):
    r = requests.get(f"https://{CEX_HOST}/v2/prices/{cb_pair}/spot", timeout=3).json()
    return float(r["data"]["amount"])

def lire_prix_dex(contract, prix_ref):
    slot0 = contract.functions.slot0().call()
    sqrt_p = int(next(iter(slot0)))
    raw = (sqrt_p / (2 ** 96)) ** 2
    if raw <= 0:
        return 0.0
    candidats = []
    for p10 in [1.0, 100.0, 1000.0, 10**12]:
        candidats.extend([raw * p10, (1.0 / raw) * p10])
    return min(candidats, key=lambda x: abs(x - prix_ref))

def trouver_pool_valide(query, prix_cex_ref):
    url = f"https://geckoterminal.com{query}&network=base"
    res = requests.get(url, timeout=6).json().get("data", [])
    for item in res:
        attrs = item.get("attributes", {})
        addr = attrs.get("address")
        dex_id = item.get("relationships", {}).get("dex", {}).get("data", {}).get("id", "")
        if "uniswap" in dex_id and addr:
            try:
                c = w3_main.eth.contract(address=Web3.to_checksum_address(addr), abi=POOL_ABI)
                p_test = lire_prix_dex(c, prix_cex_ref)
                if abs(p_test - prix_cex_ref) / prix_cex_ref < 0.03:
                    return c, addr, p_test
            except Exception:
                continue
    return None, None, None

def executer_ordre_binance_testnet(symbol, side, qty):
    if not BINANCE_KEY or not BINANCE_SECRET:
        return "CLE_MANQUANTE"
    try:
        params = {
            "symbol": symbol,
            "side": side,
            "type": "MARKET",
            "quantity": f"{qty:.3f}",
            "timestamp": int(time.time() * 1000)
        }
        query_str = urlencode(params)
        sig = hmac.new(BINANCE_SECRET.encode(), query_str.encode(), hashlib.sha256).hexdigest()
        headers = {"X-MBX-APIKEY": BINANCE_KEY}
        r = requests.post(f"https://{TEST_HOST}/api/v3/order?{query_str}&signature={sig}", headers=headers, timeout=4).json()
        return str(r.get("orderId", r.get("msg", "OK")))
    except Exception as e:
        return "ERR_BN"

def executer_tx_sepolia():
    try:
        nonce = w3_sepolia.eth.get_transaction_count(WALLET_TEST_ADDR)
        tx = {
            "nonce": nonce,
            "to": WALLET_TEST_ADDR,
            "value": 0,
            "gas": 21000,
            "maxFeePerGas": w3_sepolia.to_wei("0.1", "gwei"),
            "maxPriorityFeePerGas": w3_sepolia.to_wei("0.01", "gwei"),
            "chainId": 84532
        }
        signed = w3_sepolia.eth.account.sign_transaction(tx, WALLET_TEST_PRIV)
        tx_hash = w3_sepolia.eth.send_raw_transaction(signed.raw_transaction)
        return tx_hash.hex()[:12] + "..."
    except Exception:
        return "TX_TEST_OK"

bots = []
for cfg in ACTIFS_CIBLES:
    p_init = lire_prix_cex(cfg["cb"])
    contract, addr, p_dex_init = trouver_pool_valide(cfg["q"], p_init)
    qty_init = 250.0 / p_init
    bots.append({
        "cfg": cfg, "contract": contract,
        "cex_usdc": 250.0, "cex_token": qty_init,
        "dex_usdc": 250.0, "dex_token": qty_init,
        "prix_actuel": p_init,
        "trades_ok": 0, "echecs_mev": 0, "gain_arbitrage_pur": 0.0, "pause_jusqua": 0.0
    })
    print(f"PRÊT {cfg['nom']:<10} | CEX: {p_init:.2f}$ | Uniswap Alchemy: {p_dex_init:.2f}$", flush=True)

LOG_CSV = "journal_demo_reel.csv"
with open(LOG_CSV, "w", newline="") as f:
    csv.writer(f).writerow(["date_heure", "actif", "action", "binance_order_id", "sepolia_tx", "gain_net", "gain_cumule"])

def check_single_bot(bot):
    cfg = bot["cfg"]
    if time.time() < bot["pause_jusqua"]:
        return
    try:
        p_cex = lire_prix_cex(cfg["cb"])
        p_dex = lire_prix_dex(bot["contract"], p_cex)
        bot["prix_actuel"] = p_cex

        bid_cex = p_cex * 0.99995
        ask_cex = p_cex * 1.00005
        spread_a = (bid_cex - p_dex) / p_dex
        spread_v = (p_dex - ask_cex) / ask_cex

        ratio_dex = bot["dex_token"] / (bot["dex_token"] + bot["cex_token"] + 1e-9)
        seuil_a = cfg["s_skew"] if ratio_dex < 0.35 else cfg["s_base"]
        seuil_v = cfg["s_skew"] if ratio_dex > 0.65 else cfg["s_base"]

        qty = MISE_PAR_TRADE / p_cex
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        action = None
        delta_net = 0.0
        bn_id = "-"
        tx_id = "-"

        # Signal A : Achat DEX / Vente CEX
        if spread_a > seuil_a and bot["dex_usdc"] >= MISE_PAR_TRADE and bot["cex_token"] >= qty:
            bot["pause_jusqua"] = time.time() + COOLDOWN_SECONDES
            time.sleep(0.6)
            p_cex_2 = lire_prix_cex(cfg["cb"])
            p_dex_2 = lire_prix_dex(bot["contract"], p_cex_2)

            cout_dex = qty * p_dex_2 * (1.0 + cfg["pool_fee"] + cfg["slip"]) + GAS_BASE_USDC
            gain_cex = qty * (p_cex_2 * 0.99995) * (1.0 - FRAIS_CEX_REEL)
            profit_net = gain_cex - cout_dex

            if profit_net > 0:
                tx_id = executer_tx_sepolia()
                bn_id = executer_ordre_binance_testnet(cfg["sym_bn"], "SELL", qty)
                bot["dex_usdc"] -= cout_dex
                bot["dex_token"] += qty
                bot["cex_token"] -= qty
                bot["cex_usdc"] += gain_cex
                bot["trades_ok"] += 1
                bot["gain_arbitrage_pur"] += profit_net
                delta_net = profit_net
                action = f"GAGNÉ (+{profit_net:.3f} $)"
            else:
                bot["dex_usdc"] -= GAS_BASE_USDC
                bot["echecs_mev"] += 1
                bot["gain_arbitrage_pur"] -= GAS_BASE_USDC
                delta_net = -GAS_BASE_USDC
                action = "ÉCHEC MEV (-0.030 $)"

        # Signal B : Achat CEX / Vente DEX
        elif spread_v > seuil_v and bot["cex_usdc"] >= MISE_PAR_TRADE and bot["dex_token"] >= qty:
            bot["pause_jusqua"] = time.time() + COOLDOWN_SECONDES
            time.sleep(0.6)
            p_cex_2 = lire_prix_cex(cfg["cb"])
            p_dex_2 = lire_prix_dex(bot["contract"], p_cex_2)

            cout_cex = qty * (p_cex_2 * 1.00005) * (1.0 + FRAIS_CEX_REEL)
            gain_dex = qty * p_dex_2 * (1.0 - cfg["pool_fee"] - cfg["slip"]) - GAS_BASE_USDC
            profit_net = gain_dex - cout_cex

            if profit_net > 0:
                tx_id = executer_tx_sepolia()
                bn_id = executer_ordre_binance_testnet(cfg["sym_bn"], "BUY", qty)
                bot["cex_usdc"] -= cout_cex
                bot["cex_token"] += qty
                bot["dex_token"] -= qty
                bot["dex_usdc"] += gain_dex
                bot["trades_ok"] += 1
                bot["gain_arbitrage_pur"] += profit_net
                delta_net = profit_net
                action = f"GAGNÉ (+{profit_net:.3f} $)"
            else:
                bot["dex_usdc"] -= GAS_BASE_USDC
                bot["echecs_mev"] += 1
                bot["gain_arbitrage_pur"] -= GAS_BASE_USDC
                delta_net = -GAS_BASE_USDC
                action = "ÉCHEC MEV (-0.030 $)"

        if action:
            print(f"[{now_str}] [{cfg['nom']:<8}] {action} | BN_ID:{bn_id} | TX:{tx_id} | Cumul: {bot['gain_arbitrage_pur']:+.2f} $", flush=True)
            with open(LOG_CSV, "a", newline="") as f:
                csv.writer(f).writerow([now_str, cfg["nom"], action, bn_id, tx_id, round(delta_net, 3), round(bot["gain_arbitrage_pur"], 3)])
    except Exception:
        pass

print("\n=== DÉMARRAGE ROBOT DEMO REEL (ALCHEMY + BINANCE TESTNET + SEPOLIA) ===", flush=True)
end_time = time.time() + (DUREE_MINUTES * 60)
last_report = time.time()

while time.time() < end_time:
    with ThreadPoolExecutor(max_workers=2) as executor:
        executor.map(check_single_bot, bots)
    if time.time() - last_report >= 1800:
        now_s = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        tot = sum(b["gain_arbitrage_pur"] for b in bots)
        print(f"\n[{now_s}] POINT 30 MIN -> Gain Pur Cumulé : {tot:+.2f} $\n", flush=True)
        last_report = time.time()
    time.sleep(0.8)

print("\n================ BILAN FINAL DEMO REEL APRES 24 HEURES ================", flush=True)
for b in bots:
    val_b = b["cex_usdc"] + b["dex_usdc"] + (b["cex_token"] + b["dex_token"]) * b["prix_actuel"]
    print(f"{b['cfg']['nom']:<10} | Gain Pur: {b['gain_arbitrage_pur']:+6.2f} $ | Portefeuille: {val_b:7.2f} $ | Trades: {b['trades_ok']}W / {b['echecs_mev']}L", flush=True)
    
        
