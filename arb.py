import logging
import json
from provider import web3
from functools import reduce
import time
from decimal import Decimal
import math
from contracts import addresses, abis
import requests
from heapq import heappop, heappush, heapify

"""
TODO:
    1. optimization: use async http lib instead of web3 lib

Assumptions:
    1. reserve0 is always DAI
    2. all pools have the same pool fee and same exact code

"""

# config constants
ETH_SWAP_AMOUNT = web3.toWei(0.001, 'ether')
ETH_BLOCK_TIME = 15
LOG_LEVEL = logging.INFO

# logging
logging.basicConfig(
    format="[%(levelname)s] [%(asctime)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S UTC+0" # UTC hardcoded
)
logger = logging.getLogger(__name__)
logger.setLevel(LOG_LEVEL)

# init contracts 
def DexPool(address):
    return web3.eth.contract(
        address=web3.toChecksumAddress(address), 
        abi=abis["IUniswapV2Pair"]
    )
pools = { k: DexPool(v) for k,v in addresses["dex"].items() }

uniswap = web3.eth.contract(
    address=web3.toChecksumAddress(addresses["UniswapV2Router02"]), 
    abi=abis["UniswapV2Router02"]
)

pool_data = { k: {} for k,v in addresses["dex"].items() }
def gather_data():
    """
    gathers reserve levels and eth bid and ask quotes from all the dexes
    """

    for dex,pool in pools.items():
        dai_reserve, eth_reserve, *_ = pool.functions.getReserves().call()
        pool_data[dex]["eth"] = eth_reserve
        pool_data[dex]["dai"] = dai_reserve

        eth2dai_out = get_amount_out(ETH_SWAP_AMOUNT, eth_reserve, dai_reserve)
        eth2dai_price = eth2dai_out / ETH_SWAP_AMOUNT
        pool_data[dex]["eth2dai"] = eth2dai_price

        dai2eth_out = get_amount_out(eth2dai_out, dai_reserve, eth_reserve)
        dai2eth_price = eth2dai_out / dai2eth_out
        pool_data[dex]["dai2eth"] = dai2eth_price

        logger.debug(f"""
            {dex}
            dai_reserve: {dai_reserve}
            eth_reserve: {eth_reserve}
            eth2dai_price: {eth2dai_price}
            dai2eth_price: {dai2eth_price}
        """)

min_alloc = 1 # percent allocation out of 100
def get_pool_split(amount_in, token_in, pools=pool_data):
    """
    amount_in: amount of tokens in
    token_in: which token is going in
    pools: the pools state dict

    returns a tuple of (amount_out, pools) 
    where amount_out is the max tokens extractable from the market
    and pools is a copy of the pools dict passed in but 
    updated to include an "allocation" key for each pool that gives
    the percent of amount_in that should be sent to that pool

    the strategy is to iteratively allocate a fraction of 
    the liquidity to the pool with the best price execution
    until all the liquidity is allocated

    this can probably be optimized to constant time using
    multivariable calculus (find global extrema where the
    variables are the allocation to each pool)

    O(n log(n) + k log(n))
    k = number of allocations (usually 100 - 1000)
    n = number of pools (usually 1 - 100)
    """

    alloc_amount = math.floor(amount_in * min_alloc / 100)

    # max heap to keep track of which pool has 
    # the best swap rate for the next allocation
    pool_heap = []
    def push_pool_heap(pool_key):
        amount_out = get_amount_out_dex(alloc_amount, token_in, pool_key, pools)
        node = (-amount_out, pool_key) # negate to make max heap
        heappush(pool_heap, node)

    def pop_pool_heap():
        max_out, max_pool = heappop(pool_heap)
        return (-max_out, max_pool)
        
    for pool in pools.keys():
        push_pool_heap(pool)
    
    # get ready to distribute allocations
    pools = json.loads(json.dumps(pools))
    for pool in pools.keys():
        if not "allocation" in pools[pool]:
            pools[pool]["allocation"] = 0
    alloctions_left = 100 - sum(v["allocation"] for v in pools.values())

    # allocate all the allocations
    for i in range(0, alloctions_left, min_alloc):
        max_out, max_pool = pop_pool_heap()
        pools[max_pool]["allocation"] += min_alloc
        
        # update the reserves
        if token_in == "eth":
            pools[max_pool]["eth"] += alloc_amount
            pools[max_pool]["dai"] -= max_out
        else:
            pools[max_pool]["eth"] -= max_out 
            pools[max_pool]["dai"] += alloc_amount
        
        push_pool_heap(max_pool)

    # get the total output
    def get_pool_output(pool_key):
        amount = math.floor(amount_in * pools[pool_key]["allocation"] / 100)
        return get_amount_out_dex(amount, token_in, pool_key)
    max_out = sum(map(get_pool_output, pools.keys()))

    # remove pools that don't cover their own extra gas cost
    pools = {k:v for k,v in pools.items() if v["allocation"] > 0}
    sorted_pools = list(pools.keys())
    sorted_pools.sort(
        key=lambda pool: pools[pool]["allocation"],
        reverse=True
    )
    for pool in sorted_pools[1:]:
        pool_amount = pools[pool]["allocation"]
        without_pool = {k:v for k,v in pools.items() if k != pool}
        rebalanced_out, new_pools = get_pool_split(amount_in, token_in, without_pool)

        token_diff = max_out - rebalanced_out
        if token_in == "eth":
            token_diff = get_amount_out_dex(token_diff, "dai", "UniswapV2")
            
        if token_diff < swap_gas_fee:
            pools = new_pools # drop the pool
            max_out = rebalanced_out

    return (max_out, pools)

# @profile
def find_arbitrage():
    """
    returns (profit_in_eth, report_string)

    finds best arbitrage opportunity on the market
    """

    dai_out, eth_swaps = get_pool_split(ETH_SWAP_AMOUNT, "eth")
    eth_back, dai_swaps = get_pool_split(dai_out, "dai")
    logger.debug(f"""
        BALANCED DISTRIBUTION ALGO
        tokens_out: {dai_out}
        { json.dumps(
            { k:v["allocation"] for k,v in eth_swaps.items() }
        )}
    """)

    # calculate profitability
    swap_count = len(list(eth_swaps)) + len(list(dai_swaps))
    gas_fee = swap_gas_fee * swap_count
    profit = eth_back - ETH_SWAP_AMOUNT - gas_fee
    profit = web3.fromWei(abs(profit), 'ether') * (-1 if profit < 0 else 1)

    # prepare report
    for v in eth_swaps.values():
        v["allocation"] = str(v["allocation"]) + "%"
    for v in dai_swaps.values():
        v["allocation"] = str(v["allocation"]) + "%"

    eth_swaps = json.dumps(
        { k:v["allocation"] for k,v in eth_swaps.items() }
    )
    dai_swaps = json.dumps(
        { k:v["allocation"] for k,v in dai_swaps.items() }
    )

    report = f"""
        ARBITRAGE FOUND:
        1. swap from eth to dai using these pools: {eth_swaps}
        2. swap from dai to eth using these pools: {dai_swaps}
        3. profit: {profit} eth 
    """

    return(profit, report)
        

def get_amount_out(amount_in, reserve_in, reserve_out):
    """
    returns the token amount out given reserve levels and input amount

    X = reserve_in, Y = reserve_out
    X * Y = K
    X * Y = (X + ΔX)(Y - ΔY)
    ΔY = (Y * ΔX) / (X + ΔX)
    
    ref: https://github.com/Uniswap/v2-periphery/blob/master/contracts/libraries/UniswapV2Library.sol
    """

    amount_in_with_fee = amount_in * 997
    numerator = amount_in_with_fee * reserve_out
    denominator = (reserve_in * 1000) + amount_in_with_fee
    amount_out = numerator // denominator
    return amount_out

def get_amount_out_dex(amount_in, token_in, dex, pools=False):
    if not pools: pools = pool_data

    eth, dai = pools[dex]["eth"], pools[dex]["dai"]
    reserves = (eth, dai) if token_in == "eth" else (dai, eth)
    pool_out = get_amount_out(amount_in, *reserves)
    return pool_out


swap_gas_fee = 0
def calc_fees():
    global swap_gas_fee
    """
    determines the current cost of a dex swap in wei
    gas can be optimized with a custom contract instead of Router2
    """

    try:
        swap_tx = uniswap.functions.swapExactETHForTokens(
            0, 
            [
                web3.toChecksumAddress(addresses["token"]["WETH"]),
                web3.toChecksumAddress(addresses["token"]["DAI"])
            ],
            web3.toChecksumAddress("0x40C34a2aE15551A1d7385C5923A68CaB4A48370f"),
            web3.eth.get_block("latest")["timestamp"] + 1000
        ).buildTransaction({"value": ETH_SWAP_AMOUNT})

        gas = web3.eth.estimateGas(swap_tx)
        gasprice = get_gas_price("fastest")
        gasfee = gas * gasprice
        swap_gas_fee = gasfee

        gasfee_usd = web3.fromWei(gasfee, 'ether') * 2700
        logger.debug(f"""
            swap gas: {gas}
            swap fee: {gasprice} wei (${gasfee_usd})
        """)
    
    # fixes random error that ethgasstation throws
    except ValueError:
        time.sleep(1)
        calc_fees()

# TODO: update to EIP 1559
def get_gas_price(speed):
    gas_api = 'https://ethgasstation.info/json/ethgasAPI.json'
    response = requests.get(gas_api).json()
    gas_price_gwei = response[speed] / 10
    return web3.toWei(gas_price_gwei, 'gwei')


def main():
    max_profit = 0
    while (True):
        calc_fees()
        gather_data()
        profit, report = find_arbitrage()
        
        if profit <= 0:
            logger.info("no profitable arbitrage yet. monitoring...")

        if profit > max_profit:
            max_profit = profit
            logger.info(report)

        time.sleep(ETH_BLOCK_TIME)
main()