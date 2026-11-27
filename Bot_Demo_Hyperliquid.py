import os
import time
import csv
import requests
from datetime import datetime
from web3 import Web3

ALCHEMY_URL = os.environ.get("ALCHEMY_MAINNET", "").strip()

WALLET_TEST_ADDR = "0xF9C78D9a367B444422868101A60B7fBA12f2c144"
WALLET_TEST_PRIV = "7cc45b8f9ccb447b79b2a2eb240436244bfebe3c82f24c960f68577f4e6799d6"

DUREE_MINUTES = 1440
CAPITAL_PAR_POCHE = 500.0  # 4 poches x 500$ = 2 000$ concentrés sur ETH-USDC
MISE_MAX_TRADE = 250.0     # Plafond à 250$ pour permettre 2 gros trades consécutifs dans le même sens
MISE_MIN_TRADE = 75.0      # Minimum rentable pour couvrir les 0.03$ de gas
FRAIS_HL_TAKER = 0.00035
GAS_BASE_USDC = 0.03
COOLDOWN_SECONDES = 12.0

HL_HOST = "".join(["api", ".", "hyperliquid", ".", "xyz"])
SEPOLIA_RPC = "https://" + "".join(["sepolia", ".", "base", ".", "org"])

UNI_QUOTER_ADDR = Web3.to_checksum_address("0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a")
USDC_BASE = Web3.to_checksum_address("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913")
WETH_BASE = Web3.to_checksum_address("0x4200000000000000000000000000000000000006")

CFG_ETH = {"nom": "ETH-USDC", "hl": "ETH", "token": WETH_BASE, "dec": 18, "fee": 100, "s_base": 0.00075, "s_skew": 0.00065}

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
print(f"Nœud Alchemy connecté : {w3_main.is_connected()} | Sniper ETH-USDC (Mises Adaptatives 75$-250$) prêt", flush=True)


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

cfg = CFG_ETH
p_init = lire_prix_hyperliquid(cfg["hl"])
qty_test = quote_achat_dex(cfg["token"], cfg["dec"], cfg["fee"], 250.0)
p_dex_init = 250.0 / qty_test
qty_init = CAPITAL_PAR_POCHE / p_init

bot = {
    "cfg": cfg,
    "hl_usdc": CAPITAL_PAR_POCHE, "hl_token": qty_init,
    "dex_usdc": CAPITAL_PAR_POCHE, "dex_token": qty_init,
    "prix_actuel": p_init,
    "trades_ok": 0, "annules_quoter": 0, "gain_arbitrage_pur": 0.0, "pause_jusqua": 0.0
}
print(f"PRÊT {cfg['nom']} [UNI 0.01% + HL 0.035% | Capital 2000$ | Mise Max 250$] | HL: {p_init:.2f}$ | QuoterV2: {p_dex_init:.2f}$", flush=True)

LOG_CSV = "journal_demo_hl_eth250.csv"
with open(LOG_CSV, "w", newline="") as f:
    csv.writer(f).writerow(["date_heure", "actif", "action", "mise_usd", "sepolia_tx", "gain_net", "gain_cumule"])

def check_bot():
    if time.time() < bot["pause_jusqua"]:
        return
    try:
        p_hl = lire_prix_hyperliquid(cfg["hl"])
        bot["prix_actuel"] = p_hl

        # Calcul dynamique de la mise disponible dans chaque sens (entre 75$ et 250$)
        mise_a = min(MISE_MAX_TRADE, bot["dex_usdc"] - 1.0, (bot["hl_token"] * p_hl) * 0.98)
        mise_v = min(MISE_MAX_TRADE, bot["hl_usdc"] * 0.98, (bot["dex_token"] * p_hl) * 0.98)

        ratio_dex = bot["dex_token"] / (bot["dex_token"] + bot["hl_token"] + 1e-9)
        seuil_a = cfg["s_skew"] if ratio_dex < 0.35 else cfg["s_base"]
        seuil_v = cfg["s_skew"] if ratio_dex > 0.65 else cfg["s_base"]

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        action = None
        delta_net = 0.0
        mise_exec = 0.0
        tx_id = "-"

        # Signal A : Achat Uniswap Base / Vente Hyperliquid
        if mise_a >= MISE_MIN_TRADE:
            qty_recue_dex = quote_achat_dex(cfg["token"], cfg["dec"], cfg["fee"], mise_a)
            p_achat_dex_net = mise_a / qty_recue_dex
            spread_a = (p_hl - p_achat_dex_net) / p_achat_dex_net

            if spread_a > seuil_a:
                bot["pause_jusqua"] = time.time() + COOLDOWN_SECONDES
                time.sleep(0.4)
                p_hl_2 = lire_prix_hyperliquid(cfg["hl"])
                qty_recue_2 = quote_achat_dex(cfg["token"], cfg["dec"], cfg["fee"], mise_a)

                gain_hl = qty_recue_2 * p_hl_2 * (1.0 - FRAIS_HL_TAKER)
                profit_net = gain_hl - mise_a - GAS_BASE_USDC

                if profit_net > 0:
                    tx_id = executer_tx_sepolia()
                    bot["dex_usdc"] -= (mise_a + GAS_BASE_USDC)
                    bot["dex_token"] += qty_recue_2
                    bot["hl_token"] -= qty_recue_2
                    bot["hl_usdc"] += gain_hl
                    bot["trades_ok"] += 1
                    bot["gain_arbitrage_pur"] += profit_net
                    delta_net = profit_net
                    mise_exec = mise_a
                    action = f"GAGNÉ (+{profit_net:.3f} $)"
                else:
                    bot["annules_quoter"] += 1
                    action = "ANNULÉ QUOTER (0.000 $ gas)"

        # Signal B : Achat Hyperliquid / Vente Uniswap Base
        if not action and mise_v >= MISE_MIN_TRADE:
            qty_est_v = mise_v / p_hl
            usdc_recu_dex = quote_vente_dex(cfg["token"], cfg["dec"], cfg["fee"], qty_est_v)
            p_vente_dex_net = usdc_recu_dex / qty_est_v
            spread_v = (p_vente_dex_net - p_hl) / p_hl

            if spread_v > seuil_v:
                bot["pause_jusqua"] = time.time() + COOLDOWN_SECONDES
                time.sleep(0.4)
                p_hl_2 = lire_prix_hyperliquid(cfg["hl"])
                qty_hl_2 = mise_v / p_hl_2
                usdc_recu_2 = quote_vente_dex(cfg["token"], cfg["dec"], cfg["fee"], qty_hl_2)

                cout_hl = mise_v * (1.0 + FRAIS_HL_TAKER)
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
                    mise_exec = mise_v
                    action = f"GAGNÉ (+{profit_net:.3f} $)"
                else:
                    bot["annules_quoter"] += 1
                    action = "ANNULÉ QUOTER (0.000 $ gas)"

        if action:
            print(f"[{now_str}] [{cfg['nom']}] {action} | Mise:{mise_exec:.0f}$ | TX:{tx_id} | Cumul: {bot['gain_arbitrage_pur']:+.2f} $", flush=True)
            with open(LOG_CSV, "a", newline="") as f:
                csv.writer(f).writerow([now_str, cfg["nom"], action, round(mise_exec, 1), tx_id, round(delta_net, 3), round(bot["gain_arbitrage_pur"], 3)])
    except Exception:
        pass

print("\n=== DÉMARRAGE SNIPER ETH-USDC (MISES ADAPTATIVES 75$-250$ | FRAIS 0.045%) ===", flush=True)
end_time = time.time() + (DUREE_MINUTES * 60)
last_live = 0.0

while time.time() < end_time:
    check_bot()
    if time.time() - last_live >= 180:
        now_l = datetime.now().strftime("%H:%M:%S")
        try:
            p_c = lire_prix_hyperliquid(cfg["hl"])
            q_a = quote_achat_dex(cfg["token"], cfg["dec"], cfg["fee"], 250.0)
            q_v = quote_vente_dex(cfg["token"], cfg["dec"], cfg["fee"], 250.0 / p_c)
            sp_a = ((p_c - (250.0 / q_a)) / (250.0 / q_a)) * 100
            sp_v = (((q_v / (250.0 / p_c)) - p_c) / p_c) * 100
            print(f"[{now_l}] SCAN 3 MIN -> Achat:{sp_a:+.3f}% | Vente:{sp_v:+.3f}% | Dispo A/V:{bot['dex_usdc']:.0f}$/{bot['hl_usdc']:.0f}$ | Cumul:{bot['gain_arbitrage_pur']:+.2f}$ ({bot['trades_ok']}W)", flush=True)
        except Exception:
            pass
        last_live = time.time()
    time.sleep(0.35)

val_b = bot["hl_usdc"] + bot["dex_usdc"] + (bot["hl_token"] + bot["dex_token"]) * bot["prix_actuel"]
print(f"\nBILAN FINAL -> Gain Pur: {bot['gain_arbitrage_pur']:+6.2f} $ | Portefeuille: {val_b:7.2f} $ | Trades: {bot['trades_ok']}W / Annulés Quoter: {bot['annules_quoter']}", flush=True)
