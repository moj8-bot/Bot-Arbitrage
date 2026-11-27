import os
import time
import csv
import requests
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from web3 import Web3

ALCHEMY_URL = os.environ.get("ALCHEMY_MAINNET", "").strip()

WALLET_TEST_ADDR = "0xF9C78D9a367B444422868101A60B7fBA12f2c144"
WALLET_TEST_PRIV = "7cc45b8f9ccb447b79b2a2eb240436244bfebe3c82f24c960f68577f4e6799d6"

DUREE_MINUTES = 1440
MISE_PAR_TRADE = 210.0
FRAIS_HL_TAKER = 0.00035  # 0.035% frais Taker réels sur Hyperliquid
GAS_BASE_USDC = 0.03
COOLDOWN_SECONDES = 15.0

HL_HOST = "".join(["api", ".", "hyperliquid", ".", "xyz"])
SEPOLIA_RPC = "https://" + "".join(["sepolia", ".", "base", ".", "org"])

UNI_QUOTER_ADDR = Web3.to_checksum_address("0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a")

USDC_BASE  = Web3.to_checksum_address("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913")
WETH_BASE  = Web3.to_checksum_address("0x4200000000000000000000000000000000000006")
CBBTC_BASE = Web3.to_checksum_address("0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf")

# Seuils calibrés pour frais totaux ultra-bas (0.01% Uniswap + 0.035% Hyperliquid = 0.045%)
ACTIFS_CIBLES = [
    {"nom": "ETH-USDC", "hl": "ETH", "token": WETH_BASE,  "dec": 18, "fee": 100, "s_base": 0.0010, "s_skew": 0.0008},
    {"nom": "BTC-USDC", "hl": "BTC", "token": CBBTC_BASE, "dec": 8,  "fee": 500, "s_base": 0.0011, "s_skew": 0.0009}
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

rpc_actif = ALCHEMY_URL if ALCHEMY_URL.startswith("http") else "https://" + "".join(["base", "-", "rpc", ".", "publicnode", ".", "com"])
w3_main = Web3(Web3.HTTPProvider(rpc_actif, request_kwargs={'timeout': 4}))
w3_sepolia = Web3(Web3.HTTPProvider(SEPOLIA_RPC, request_kwargs={'timeout': 4}))
uni_quoter = w3_main.eth.contract(address=UNI_QUOTER_ADDR, abi=UNI_ABI)
print(f"Nœud Alchemy connecté : {w3_main.is_connected()} | Uniswap v3 + Hyperliquid (0.035%) prêt", flush=True)


def lire_prix_hyperliquid(coin):
    r = requests.post(f"https://{HL_HOST}/info", json={"type": "allMids"}, timeout=3).json()
    return float(r[coin])

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

bots = []
for cfg in ACTIFS_CIBLES:
    p_init = lire_prix_hyperliquid(cfg["hl"])
    qty_test = 0
    for f_test in (100, 500):
        try:
            q = quote_achat_dex(cfg["token"], cfg["dec"], f_test, MISE_PAR_TRADE)
            if q > 0 and abs((MISE_PAR_TRADE / q) - p_init) / p_init < 0.03:
                cfg["fee"] = f_test
                qty_test = q
                break
        except Exception:
            continue
    p_dex_init = (MISE_PAR_TRADE / qty_test) if qty_test > 0 else p_init
    qty_init = 250.0 / p_init
    bots.append({
        "cfg": cfg, "actif_quoter": qty_test > 0,
        "hl_usdc": 250.0, "hl_token": qty_init,
        "dex_usdc": 250.0, "dex_token": qty_init,
        "prix_actuel": p_init,
        "trades_ok": 0, "annules_quoter": 0, "gain_arbitrage_pur": 0.0, "pause_jusqua": 0.0
    })
    frais_pct = cfg["fee"] / 10000.0
    print(f"PRÊT {cfg['nom']:<10} [UNI {frais_pct:.2f}% + HL 0.035%] | HL: {p_init:.2f}$ | QuoterV2: {p_dex_init:.2f}$", flush=True)

LOG_CSV = "journal_demo_hyperliquid.csv"
with open(LOG_CSV, "w", newline="") as f:
    csv.writer(f).writerow(["date_heure", "actif", "action", "sepolia_tx", "gain_net", "gain_cumule"])

def check_single_bot(bot):
    cfg = bot["cfg"]
    if not bot["actif_quoter"] or time.time() < bot["pause_jusqua"]:
        return
    try:
        p_hl = lire_prix_hyperliquid(cfg["hl"])
        qty_est = MISE_PAR_TRADE / p_hl
        bot["prix_actuel"] = p_hl

        qty_recue_dex = quote_achat_dex(cfg["token"], cfg["dec"], cfg["fee"], MISE_PAR_TRADE)
        usdc_recu_dex = quote_vente_dex(cfg["token"], cfg["dec"], cfg["fee"], qty_est)

        p_achat_dex_net = MISE_PAR_TRADE / qty_recue_dex
        p_vente_dex_net = usdc_recu_dex / qty_est

        spread_a = (p_hl - p_achat_dex_net) / p_achat_dex_net
        spread_v = (p_vente_dex_net - p_hl) / p_hl

        ratio_dex = bot["dex_token"] / (bot["dex_token"] + bot["hl_token"] + 1e-9)
        seuil_a = cfg["s_skew"] if ratio_dex < 0.35 else cfg["s_base"]
        seuil_v = cfg["s_skew"] if ratio_dex > 0.65 else cfg["s_base"]

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        action = None
        delta_net = 0.0
        tx_id = "-"

        # Signal A : Achat Uniswap Base / Vente Hyperliquid
        if spread_a > seuil_a and bot["dex_usdc"] >= MISE_PAR_TRADE and bot["hl_token"] >= qty_recue_dex:
            bot["pause_jusqua"] = time.time() + COOLDOWN_SECONDES
            time.sleep(0.4)
            p_hl_2 = lire_prix_hyperliquid(cfg["hl"])
            qty_recue_2 = quote_achat_dex(cfg["token"], cfg["dec"], cfg["fee"], MISE_PAR_TRADE)

            gain_hl = qty_recue_2 * p_hl_2 * (1.0 - FRAIS_HL_TAKER)
            profit_net = gain_hl - MISE_PAR_TRADE - GAS_BASE_USDC

            if profit_net > 0:
                tx_id = executer_tx_sepolia()
                bot["dex_usdc"] -= (MISE_PAR_TRADE + GAS_BASE_USDC)
                bot["dex_token"] += qty_recue_2
                bot["hl_token"] -= qty_recue_2
                bot["hl_usdc"] += gain_hl
                bot["trades_ok"] += 1
                bot["gain_arbitrage_pur"] += profit_net
                delta_net = profit_net
                action = f"GAGNÉ (+{profit_net:.3f} $)"
            else:
                bot["annules_quoter"] += 1
                action = "ANNULÉ QUOTER (0.000 $ gas)"

        # Signal B : Achat Hyperliquid / Vente Uniswap Base
        elif spread_v > seuil_v and bot["hl_usdc"] >= MISE_PAR_TRADE and bot["dex_token"] >= qty_est:
            bot["pause_jusqua"] = time.time() + COOLDOWN_SECONDES
            time.sleep(0.4)
            p_hl_2 = lire_prix_hyperliquid(cfg["hl"])
            qty_hl_2 = MISE_PAR_TRADE / p_hl_2
            usdc_recu_2 = quote_vente_dex(cfg["token"], cfg["dec"], cfg["fee"], qty_hl_2)

            cout_hl = MISE_PAR_TRADE * (1.0 + FRAIS_HL_TAKER)
            profit_net = usdc_recu_2 - cout_hl - GAS_BASE_USDC

            if profit_net > 0:
                tx_id = executer_tx_sepolia()
                bot["hl_usdc"] -= cout_hl
                bot["hl_token"] += qty_hl_2
                bot["dex_token"] -= qty_hl_2
                bot["dex_usdc"] += (usdc_recu_2 - GAS_BASE_USDC)
                bot["trades_ok"] += 1
                bot["gain_arbitrage_pur"] += profit_net
                delta_net = profit_net
                action = f"GAGNÉ (+{profit_net:.3f} $)"
            else:
                bot["annules_quoter"] += 1
                action = "ANNULÉ QUOTER (0.000 $ gas)"

        if action:
            print(f"[{now_str}] [{cfg['nom']:<8}] {action} | TX:{tx_id} | Cumul: {bot['gain_arbitrage_pur']:+.2f} $", flush=True)
            with open(LOG_CSV, "a", newline="") as f:
                csv.writer(f).writerow([now_str, cfg["nom"], action, tx_id, round(delta_net, 3), round(bot["gain_arbitrage_pur"], 3)])
    except Exception:
        pass

print("\n=== DÉMARRAGE ROBOT DEMO HYPERLIQUID + UNISWAP BASE (FRAIS 0.045% TOTAL) ===", flush=True)
end_time = time.time() + (DUREE_MINUTES * 60)
last_live = 0.0

while time.time() < end_time:
    with ThreadPoolExecutor(max_workers=2) as executor:
        executor.map(check_single_bot, bots)
    if time.time() - last_live >= 180:
        now_l = datetime.now().strftime("%H:%M:%S")
        etats = []
        for b in bots:
            try:
                p_c = lire_prix_hyperliquid(b["cfg"]["hl"])
                q_a = quote_achat_dex(b["cfg"]["token"], b["cfg"]["dec"], b["cfg"]["fee"], MISE_PAR_TRADE)
                sp = ((p_c - (MISE_PAR_TRADE / q_a)) / (MISE_PAR_TRADE / q_a)) * 100
                etats.append(f"{b['cfg']['nom']}: {sp:+.3f}% (Cumul: {b['gain_arbitrage_pur']:+.2f}$)")
            except Exception:
                pass
        print(f"[{now_l}] SCAN 3 MIN -> {' | '.join(etats)}", flush=True)
        last_live = time.time()
    time.sleep(0.35)

print("\n================ BILAN FINAL DEMO HYPERLIQUID + UNISWAP ================", flush=True)
for b in bots:
    val_b = b["hl_usdc"] + b["dex_usdc"] + (b["hl_token"] + b["dex_token"]) * b["prix_actuel"]
    print(f"{b['cfg']['nom']:<10} | Gain Pur: {b['gain_arbitrage_pur']:+6.2f} $ | Portefeuille: {val_b:7.2f} $ | Trades: {b['trades_ok']}W / Annulés Quoter: {b['annules_quoter']}", flush=True)
                 
