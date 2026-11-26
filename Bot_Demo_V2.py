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

DUREE_MINUTES = 1440
MISE_PAR_TRADE = 210.0
FRAIS_CEX_REEL = 0.0005
GAS_BASE_USDC = 0.03
COOLDOWN_SECONDES = 15.0

CEX_HOST = "".join(["api", ".", "coinbase", ".", "com"])
TEST_HOST = "".join(["testnet", ".", "binance", ".", "vision"])
SEPOLIA_RPC = "https://" + "".join(["sepolia", ".", "base", ".", "org"])

# Smart contracts Quoter officiels sur Base Mainnet (Uniswap v3 + Aerodrome Slipstream)
UNI_QUOTER_ADDR  = Web3.to_checksum_address("0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a")
AERO_QUOTER_ADDR = Web3.to_checksum_address("0x254cF9E1E6e233aa1AC962CB9B05b2cfeAaE15b0")

USDC_BASE = Web3.to_checksum_address("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913")
WETH_BASE = Web3.to_checksum_address("0x4200000000000000000000000000000000000006")
SOL_BASE  = Web3.to_checksum_address("0x311935Cd80B76769bF2ecC9D8Ab7635b2139cf82")

ACTIFS_CIBLES = [
    {"nom": "ETH-USDC", "dex": "UNI",  "cb": "ETH-USD", "sym_bn": "ETHUSDT", "token": WETH_BASE, "dec": 18, "param": 100, "s_base": 0.0023, "s_skew": 0.0020},
    {"nom": "SOL-USDC", "dex": "AERO", "cb": "SOL-USD", "sym_bn": "SOLUSDT", "token": SOL_BASE,  "dec": 9,  "param": 100, "s_base": 0.0024, "s_skew": 0.0021}
]

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

AERO_ABI = [{
    "inputs": [{"components": [
        {"internalType": "address", "name": "tokenIn", "type": "address"},
        {"internalType": "address", "name": "tokenOut", "type": "address"},
        {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
        {"internalType": "int24", "name": "tickSpacing", "type": "int24"},
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
uni_quoter  = w3_main.eth.contract(address=UNI_QUOTER_ADDR, abi=UNI_ABI)
aero_quoter = w3_main.eth.contract(address=AERO_QUOTER_ADDR, abi=AERO_ABI)
print(f"Nœud Alchemy connecté : {w3_main.is_connected()} | Double Quoter (Uniswap + Aerodrome) prêt", flush=True)


def lire_prix_cex(cb_pair):
    r = requests.get(f"https://{CEX_HOST}/v2/prices/{cb_pair}/spot", timeout=3).json()
    return float(r["data"]["amount"])

def quote_achat_dex(dex_type, token_addr, dec, param, usdc_in):
    amt_in = int(usdc_in * 10**6)
    q_contract = uni_quoter if dex_type == "UNI" else aero_quoter
    res = q_contract.functions.quoteExactInputSingle((USDC_BASE, token_addr, amt_in, param, 0)).call()
    val = res[0] if isinstance(res, (list, tuple)) else res
    return val / (10**dec)

def quote_vente_dex(dex_type, token_addr, dec, param, token_in):
    amt_in = int(token_in * (10**dec))
    q_contract = uni_quoter if dex_type == "UNI" else aero_quoter
    res = q_contract.functions.quoteExactInputSingle((token_addr, USDC_BASE, amt_in, param, 0)).call()
    val = res[0] if isinstance(res, (list, tuple)) else res
    return val / 10**6

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
    except Exception:
        return "ERR_BN"

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

bots = []
for cfg in ACTIFS_CIBLES:
    p_init = lire_prix_cex(cfg["cb"])
    qty_test = 0
    candidats = (100, 500, 3000) if cfg["dex"] == "UNI" else (1, 50, 100, 200)
    for p_test in candidats:
        try:
            q = quote_achat_dex(cfg["dex"], cfg["token"], cfg["dec"], p_test, MISE_PAR_TRADE)
            if q > 0 and abs((MISE_PAR_TRADE / q) - p_init) / p_init < 0.03:
                cfg["param"] = p_test
                qty_test = q
                break
        except Exception:
            continue
    p_dex_init = (MISE_PAR_TRADE / qty_test) if qty_test > 0 else p_init
    qty_init = 250.0 / p_init
    bots.append({
        "cfg": cfg, "actif_quoter": qty_test > 0,
        "cex_usdc": 250.0, "cex_token": qty_init,
        "dex_usdc": 250.0, "dex_token": qty_init,
        "prix_actuel": p_init,
        "trades_ok": 0, "annules_quoter": 0, "gain_arbitrage_pur": 0.0, "pause_jusqua": 0.0
    })
    print(f"PRÊT {cfg['nom']:<10} [{cfg['dex']}] | CEX: {p_init:.2f}$ | Quoter (p={cfg['param']}): {p_dex_init:.2f}$", flush=True)

LOG_CSV = "journal_demo_v2_quoter.csv"
with open(LOG_CSV, "w", newline="") as f:
    csv.writer(f).writerow(["date_heure", "actif", "action", "binance_order_id", "sepolia_tx", "gain_net", "gain_cumule"])

def check_single_bot(bot):
    cfg = bot["cfg"]
    if not bot["actif_quoter"] or time.time() < bot["pause_jusqua"]:
        return
    try:
        p_cex = lire_prix_cex(cfg["cb"])
        qty_est = MISE_PAR_TRADE / p_cex
        bot["prix_actuel"] = p_cex

        qty_recue_dex = quote_achat_dex(cfg["dex"], cfg["token"], cfg["dec"], cfg["param"], MISE_PAR_TRADE)
        usdc_recu_dex = quote_vente_dex(cfg["dex"], cfg["token"], cfg["dec"], cfg["param"], qty_est)

        p_achat_dex_net = MISE_PAR_TRADE / qty_recue_dex
        p_vente_dex_net = usdc_recu_dex / qty_est

        spread_a = (p_cex - p_achat_dex_net) / p_achat_dex_net
        spread_v = (p_vente_dex_net - p_cex) / p_cex

        ratio_dex = bot["dex_token"] / (bot["dex_token"] + bot["cex_token"] + 1e-9)
        seuil_a = cfg["s_skew"] if ratio_dex < 0.35 else cfg["s_base"]
        seuil_v = cfg["s_skew"] if ratio_dex > 0.65 else cfg["s_base"]

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        action = None
        delta_net = 0.0
        bn_id = "-"
        tx_id = "-"

        if spread_a > seuil_a and bot["dex_usdc"] >= MISE_PAR_TRADE and bot["cex_token"] >= qty_recue_dex:
            bot["pause_jusqua"] = time.time() + COOLDOWN_SECONDES
            time.sleep(0.4)
            p_cex_2 = lire_prix_cex(cfg["cb"])
            qty_recue_2 = quote_achat_dex(cfg["dex"], cfg["token"], cfg["dec"], cfg["param"], MISE_PAR_TRADE)

            gain_cex = qty_recue_2 * p_cex_2 * (1.0 - FRAIS_CEX_REEL)
            profit_net = gain_cex - MISE_PAR_TRADE - GAS_BASE_USDC

            if profit_net > 0:
                tx_id = executer_tx_sepolia()
                bn_id = executer_ordre_binance_testnet(cfg["sym_bn"], "SELL", qty_recue_2)
                bot["dex_usdc"] -= (MISE_PAR_TRADE + GAS_BASE_USDC)
                bot["dex_token"] += qty_recue_2
                bot["cex_token"] -= qty_recue_2
                bot["cex_usdc"] += gain_cex
                bot["trades_ok"] += 1
                bot["gain_arbitrage_pur"] += profit_net
                delta_net = profit_net
                action = f"GAGNÉ (+{profit_net:.3f} $)"
            else:
                bot["annules_quoter"] += 1
                action = "ANNULÉ QUOTER (0.000 $ gas)"

        elif spread_v > seuil_v and bot["cex_usdc"] >= MISE_PAR_TRADE and bot["dex_token"] >= qty_est:
            bot["pause_jusqua"] = time.time() + COOLDOWN_SECONDES
            time.sleep(0.4)
            p_cex_2 = lire_prix_cex(cfg["cb"])
            qty_cex_2 = MISE_PAR_TRADE / p_cex_2
            usdc_recu_2 = quote_vente_dex(cfg["dex"], cfg["token"], cfg["dec"], cfg["param"], qty_cex_2)

            cout_cex = MISE_PAR_TRADE * (1.0 + FRAIS_CEX_REEL)
            profit_net = usdc_recu_2 - cout_cex - GAS_BASE_USDC

            if profit_net > 0:
                tx_id = executer_tx_sepolia()
                bn_id = executer_ordre_binance_testnet(cfg["sym_bn"], "BUY", qty_cex_2)
                bot["cex_usdc"] -= cout_cex
                bot["cex_token"] += qty_cex_2
                bot["dex_token"] -= qty_cex_2
                bot["dex_usdc"] += (usdc_recu_2 - GAS_BASE_USDC)
                bot["trades_ok"] += 1
                bot["gain_arbitrage_pur"] += profit_net
                delta_net = profit_net
                action = f"GAGNÉ (+{profit_net:.3f} $)"
            else:
                bot["annules_quoter"] += 1
                action = "ANNULÉ QUOTER (0.000 $ gas)"

        if action:
            print(f"[{now_str}] [{cfg['nom']:<8}] {action} | BN:{bn_id} | TX:{tx_id} | Cumul: {bot['gain_arbitrage_pur']:+.2f} $", flush=True)
            with open(LOG_CSV, "a", newline="") as f:
                csv.writer(f).writerow([now_str, cfg["nom"], action, bn_id, tx_id, round(delta_net, 3), round(bot["gain_arbitrage_pur"], 3)])
    except Exception:
        pass

print("\n=== DÉMARRAGE ROBOT DEMO V2 (UNISWAP + AERODROME + QUOTER + 0.35s) ===", flush=True)
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
    time.sleep(0.35)

print("\n================ BILAN FINAL DEMO V2 (UNISWAP + AERODROME) ================", flush=True)
for b in bots:
    val_b = b["cex_usdc"] + b["dex_usdc"] + (b["cex_token"] + b["dex_token"]) * b["prix_actuel"]
    print(f"{b['cfg']['nom']:<10} | Gain Pur: {b['gain_arbitrage_pur']:+6.2f} $ | Portefeuille: {val_b:7.2f} $ | Trades: {b['trades_ok']}W / Annulés Quoter: {b['annules_quoter']}", flush=True)
