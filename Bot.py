import time
import csv
import requests
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from web3 import Web3

# --- CONFIGURATION RÉALISTE (2 000 $ TOTAL SUR 2 ACTIFS - ZÉRO TRANSFERT PAYANT) ---
DUREE_MINUTES = 1440          # 24 heures
CAPITAL_PAR_ACTIF = 1000.0    # 1 000 $ par actif (250 $ par poche x 4)
MISE_PAR_TRADE = 110.0        # 110 $ par trade -> 2 balles par sens

FRAIS_CEX_REEL = 0.0005       # 0.05 % (exécution immédiate Taker réaliste)
GAS_BASE_USDC = 0.03          # 0.03 $ par swap sur Base
COOLDOWN_SECONDES = 15.0      # Verrou anti-doublon après chaque trade

CEX_HOST = "".join(["api", ".", "coinbase", ".", "com"])
GECKO_HOST = "".join(["api", ".", "geckoterminal", ".", "com"])

ACTIFS_CIBLES = [
    {"nom": "ETH-USDC", "cb": "ETH-USD", "q": "WETH USDC", "pool_fee": 0.0005, "slip": 0.0005, "s_base": 0.0023, "s_skew": 0.0019},
    {"nom": "SOL-USDC", "cb": "SOL-USD", "q": "SOL USDC",  "pool_fee": 0.0005, "slip": 0.0006, "s_base": 0.0024, "s_skew": 0.0020}
]

RPC_LIST = [
    "https://" + "".join(["base", "-", "rpc", ".", "publicnode", ".", "com"]),
    "https://" + "".join(["base", ".", "llamarpc", ".", "com"]),
    "https://" + "".join(["1rpc", ".", "io"]) + "/base"
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

w3 = None
for rpc in RPC_LIST:
    try:
        w3_test = Web3(Web3.HTTPProvider(rpc, request_kwargs={'timeout': 5}))
        if w3_test.is_connected():
            w3 = w3_test
            print(f"RPC Base connecté : {rpc}\n", flush=True)
            break
    except Exception:
        continue

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
    url = f"https://{GECKO_HOST}/api/v2/search/pools?query={query}&network=base"
    res = requests.get(url, timeout=6).json().get("data", [])
    for item in res:
        attrs = item.get("attributes", {})
        addr = attrs.get("address")
        dex_id = item.get("relationships", {}).get("dex", {}).get("data", {}).get("id", "")
        if "uniswap" in dex_id and addr:
            try:
                c = w3.eth.contract(address=Web3.to_checksum_address(addr), abi=POOL_ABI)
                p_test = lire_prix_dex(c, prix_cex_ref)
                if abs(p_test - prix_cex_ref) / prix_cex_ref < 0.03:
                    return c, addr, p_test
            except Exception:
                continue
    return None, None, None

bots = []
for cfg in ACTIFS_CIBLES:
    r = requests.get(f"https://{CEX_HOST}/v2/prices/{cfg['cb']}/spot", timeout=5).json()
    p_init = float(r["data"]["amount"])
    contract, addr, p_dex_init = trouver_pool_valide(cfg["q"], p_init)
    
    qty_init = 250.0 / p_init
    bots.append({
        "cfg": cfg,
        "contract": contract,
        "cex_usdc": 250.0,
        "cex_token": qty_init,
        "dex_usdc": 250.0,
        "dex_token": qty_init,
        "prix_actuel": p_init,
        "trades_ok": 0,
        "echecs_mev": 0,
        "frais_payes": 0.0,
        "gain_arbitrage_pur": 0.0,
        "pause_jusqua": 0.0
    })
    print(f"PRÊT {cfg['nom']:<10} | Capital: 1000.00 $ (250$ USDC + {qty_init:.4f} {cfg['nom'].split('-')} par côté)", flush=True)
LOG_CSV = "simulation_propre_2000.csv"
with open(LOG_CSV, "w", newline="") as f:
    csv.writer(f).writerow(["date_heure", "actif", "action", "gain_net_trade", "gain_arb_cumule", "valeur_portefeuille"])

def check_single_bot(bot):
    cfg = bot["cfg"]
    if time.time() < bot["pause_jusqua"]:
        return

    try:
        p_cex = float(requests.get(f"https://{CEX_HOST}/v2/prices/{cfg['cb']}/spot", timeout=3).json()["data"]["amount"])
        p_dex = lire_prix_dex(bot["contract"], p_cex)
        bot["prix_actuel"] = p_cex

        bid_cex = p_cex * 0.99995
        ask_cex = p_cex * 1.00005
        spread_a = (bid_cex - p_dex) / p_dex
        spread_v = (p_dex - ask_cex) / ask_cex

        # Skewing naturel : baisse le seuil dans le sens inverse si un côté a pris ses 2 balles
        ratio_dex = bot["dex_token"] / (bot["dex_token"] + bot["cex_token"] + 1e-9)
        seuil_a = cfg["s_skew"] if ratio_dex < 0.35 else cfg["s_base"]
        seuil_v = cfg["s_skew"] if ratio_dex > 0.65 else cfg["s_base"]

        qty = MISE_PAR_TRADE / p_cex
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        action = None
        delta_net = 0.0

        # Signal A : Achat DEX / Vente CEX
        if spread_a > seuil_a and bot["dex_usdc"] >= MISE_PAR_TRADE and bot["cex_token"] >= qty:
            bot["pause_jusqua"] = time.time() + COOLDOWN_SECONDES
            time.sleep(1.5)
            p_cex_2 = float(requests.get(f"https://{CEX_HOST}/v2/prices/{cfg['cb']}/spot", timeout=3).json()["data"]["amount"])
            p_dex_2 = lire_prix_dex(bot["contract"], p_cex_2)
            
            cout_dex = qty * p_dex_2 * (1.0 + cfg["pool_fee"] + cfg["slip"]) + GAS_BASE_USDC
            gain_cex = qty * (p_cex_2 * 0.99995) * (1.0 - FRAIS_CEX_REEL)
            profit_net = gain_cex - cout_dex
            
            if profit_net > 0:
                bot["dex_usdc"] -= cout_dex
                bot["dex_token"] += qty
                bot["cex_token"] -= qty
                bot["cex_usdc"] += gain_cex
                bot["trades_ok"] += 1
                frais_trade = qty * p_dex_2 * (cfg["pool_fee"] + cfg["slip"]) + GAS_BASE_USDC + qty * p_cex_2 * FRAIS_CEX_REEL
                bot["frais_payes"] += frais_trade
                bot["gain_arbitrage_pur"] += profit_net
                delta_net = profit_net
                action = f"GAGNÉ (+{profit_net:.3f} $)"
            else:
                bot["dex_usdc"] -= GAS_BASE_USDC
                bot["echecs_mev"] += 1
                bot["frais_payes"] += GAS_BASE_USDC
                bot["gain_arbitrage_pur"] -= GAS_BASE_USDC
                delta_net = -GAS_BASE_USDC
                action = "ÉCHEC MEV (-0.030 $)"

        # Signal B : Achat CEX / Vente DEX
        elif spread_v > seuil_v and bot["cex_usdc"] >= MISE_PAR_TRADE and bot["dex_token"] >= qty:
            bot["pause_jusqua"] = time.time() + COOLDOWN_SECONDES
            time.sleep(1.5)
            p_cex_2 = float(requests.get(f"https://{CEX_HOST}/v2/prices/{cfg['cb']}/spot", timeout=3).json()["data"]["amount"])
            p_dex_2 = lire_prix_dex(bot["contract"], p_cex_2)
            
            cout_cex = qty * (p_cex_2 * 1.00005) * (1.0 + FRAIS_CEX_REEL)
            gain_dex = qty * p_dex_2 * (1.0 - cfg["pool_fee"] - cfg["slip"]) - GAS_BASE_USDC
            profit_net = gain_dex - cout_cex
            
            if profit_net > 0:
                bot["cex_usdc"] -= cout_cex
                bot["cex_token"] += qty
                bot["dex_token"] -= qty
                bot["dex_usdc"] += gain_dex
                bot["trades_ok"] += 1
                frais_trade = qty * p_cex_2 * FRAIS_CEX_REEL + qty * p_dex_2 * (cfg["pool_fee"] + cfg["slip"]) + GAS_BASE_USDC
                bot["frais_payes"] += frais_trade
                bot["gain_arbitrage_pur"] += profit_net
                delta_net = profit_net
                action = f"GAGNÉ (+{profit_net:.3f} $)"
            else:
                bot["dex_usdc"] -= GAS_BASE_USDC
                bot["echecs_mev"] += 1
                bot["frais_payes"] += GAS_BASE_USDC
                bot["gain_arbitrage_pur"] -= GAS_BASE_USDC
                delta_net = -GAS_BASE_USDC
                action = "ÉCHEC MEV (-0.030 $)"

        val_tot = bot["cex_usdc"] + bot["dex_usdc"] + (bot["cex_token"] + bot["dex_token"]) * p_cex

        if action:
            print(f"[{now_str}] [{cfg['nom']:<8}] {action} | Gain Pur Cumulé: {bot['gain_arbitrage_pur']:+.2f} $ | Portefeuille: {val_tot:.2f} $", flush=True)
            with open(LOG_CSV, "a", newline="") as f:
                csv.writer(f).writerow([now_str, cfg["nom"], action, round(delta_net, 3), round(bot["gain_arbitrage_pur"], 3), round(val_tot, 2)])

    except Exception:
        pass

print(f"\n=== DÉMARRAGE DU TEST RÉALISTE 24H (2 000 $ | MISES 110 $ | SANS TRANSFERT PAYANT) ===", flush=True)
end_time = time.time() + (DUREE_MINUTES * 60)
last_report = time.time()

while time.time() < end_time:
    with ThreadPoolExecutor(max_workers=2) as executor:
        executor.map(check_single_bot, bots)
    
    if time.time() - last_report >= 1800:
        now_s = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        gain_pur_tot = sum(b["gain_arbitrage_pur"] for b in bots)
        val_tot_globale = sum(b["cex_usdc"] + b["dex_usdc"] + (b["cex_token"] + b["dex_token"]) * b["prix_actuel"] for b in bots)
        print(f"\n[{now_s}] POINT 30 MIN -> Gain Pur Arbitrage: {gain_pur_tot:+.2f} $ | Valeur Portefeuille: {val_tot_globale:.2f} $\n", flush=True)
        last_report = time.time()
        
    time.sleep(2.5)

print("\n================ BILAN FINAL RÉALISTE APRÈS 24 HEURES ================", flush=True)
gain_pur_final = 0.0
val_portefeuille_final = 0.0
for b in bots:
    val_b = b["cex_usdc"] + b["dex_usdc"] + (b["cex_token"] + b["dex_token"]) * b["prix_actuel"]
    gain_pur_final += b["gain_arbitrage_pur"]
    val_portefeuille_final += val_b
    print(f"{b['cfg']['nom']:<10} | Gain Pur: {b['gain_arbitrage_pur']:+6.2f} $ | Portefeuille: {val_b:7.2f} $ | Trades: {b['trades_ok']}W / {b['echecs_mev']}L | Frais: {b['frais_payes']:.2f} $", flush=True)

print(f"\nGAIN PUR DE L'ARBITRAGE (24H, TOUS FRAIS DÉDUITS) : {gain_pur_final:+.2f} $")
print(f"VALEUR TOTALE DU PORTEFEUILLE (2 000 $ DÉPART)     : {val_portefeuille_final:.2f} $")
            
