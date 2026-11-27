import os
import requests
from web3 import Web3

HL_HOST = "".join(["api", ".", "hyperliquid", ".", "xyz"])
rpc = os.environ.get("ALCHEMY_MAINNET", "").strip()
if not rpc.startswith("http"):
    rpc = "https://" + "".join(["base", "-", "rpc", ".", "publicnode", ".", "com"])

w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={'timeout': 5}))
USDC = Web3.to_checksum_address("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913")

UNI_Q = w3.eth.contract(address=Web3.to_checksum_address("0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a"), abi=[{"inputs":[{"components":[{"internalType":"address","name":"tokenIn","type":"address"},{"internalType":"address","name":"tokenOut","type":"address"},{"internalType":"uint256","name":"amountIn","type":"uint256"},{"internalType":"uint24","name":"fee","type":"uint24"},{"internalType":"uint160","name":"sqrtPriceLimitX96","type":"uint160"}],"internalType":"struct IQuoterV2.QuoteExactInputSingleParams","name":"params","type":"tuple"}],"name":"quoteExactInputSingle","outputs":[{"internalType":"uint256","name":"amountOut","type":"uint256"},{"internalType":"uint160","name":"sqrtPriceX96After","type":"uint160"},{"internalType":"uint32","name":"initializedTicksCrossed","type":"uint32"},{"internalType":"uint256","name":"gasEstimate","type":"uint256"}],"stateMutability":"nonpayable","type":"function"}])
AERO_Q = w3.eth.contract(address=Web3.to_checksum_address("0x254cF9E1E6e233aa1AC962CB9B05b2cfeAaE15b0"), abi=[{"inputs":[{"components":[{"internalType":"address","name":"tokenIn","type":"address"},{"internalType":"address","name":"tokenOut","type":"address"},{"internalType":"uint256","name":"amountIn","type":"uint256"},{"internalType":"int24","name":"tickSpacing","type":"int24"},{"internalType":"uint160","name":"sqrtPriceLimitX96","type":"uint160"}],"internalType":"struct IQuoterV2.QuoteExactInputSingleParams","name":"params","type":"tuple"}],"name":"quoteExactInputSingle","outputs":[{"internalType":"uint256","name":"amountOut","type":"uint256"},{"internalType":"uint160","name":"sqrtPriceX96After","type":"uint160"},{"internalType":"uint32","name":"initializedTicksCrossed","type":"uint32"},{"internalType":"uint256","name":"gasEstimate","type":"uint256"}],"stateMutability":"nonpayable","type":"function"}])

mids = requests.post(f"https://{HL_HOST}/info", json={"type": "allMids"}, timeout=5).json()
tokens = [
    ("ETH",     "0x4200000000000000000000000000000000000006"),
    ("AERO",    "0x940181a94A35A4569E4529A3CDfB74e38FD98631"),
    ("VIRTUAL", "0x0b3e328455c4059EEb9e3f84b5543F74E24e7E1b"),
    ("BRETT",   "0x532f27101965dd16442E59d40670FaF5eBB142E4"),
]

print("\n=== SCAN DIRECT HYPERLIQUID <-> BASE (UNISWAP + AERODROME) POUR 200$ ===", flush=True)
for sym, addr in tokens:
    if sym not in mids:
        print(f"{sym:<8}: Non coté sur Hyperliquid Perps", flush=True)
        continue
    p_hl = float(mids[sym])
    t_addr = Web3.to_checksum_address(addr)
    best_cost = 999.0
    best_line = None
    for dex_name, contract, params in [("UNI", UNI_Q, (100, 500, 3000)), ("AERO", AERO_Q, (10, 50, 100, 200))]:
        for p in params:
            try:
                res = contract.functions.quoteExactInputSingle((USDC, t_addr, 200 * 10**6, p, 0)).call()
                qty = int(next(iter(res))) / 10**18
                p_buy = 200.0 / qty
                if abs(p_buy - p_hl) / p_hl < 0.05:
                    res_v = contract.functions.quoteExactInputSingle((t_addr, USDC, int((200.0/p_hl)*10**18), p, 0)).call()
                    usdc_sell = int(next(iter(res_v))) / 10**6
                    p_sell = usdc_sell / (200.0 / p_hl)
                    sp_a = ((p_hl - p_buy) / p_buy) * 100
                    sp_v = ((p_sell - p_hl) / p_hl) * 100
                    cout_tot = (abs(p_buy - p_sell) / (2 * p_hl)) * 100
                    if cout_tot < best_cost:
                        best_cost = cout_tot
                        best_line = f"{sym:<8} | HL:{p_hl:<8.4f}$ | {dex_name} (p={p}) | Frais DEX:{cout_tot:.3f}% | Achat:{sp_a:+.3f}% | Vente:{sp_v:+.3f}%"
            except Exception:
                pass
    print(best_line if best_line else f"{sym:<8} | HL:{p_hl:<8.4f}$ | Aucun pool liquide trouvé", flush=True)
  
