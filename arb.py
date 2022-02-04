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

logging.basicConfig(
    format="[%(levelname)s] [%(asctime)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S UTC+0" # UTC hardcoded
)
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# TODO:
# 
# 1. optimization: use async http lib instead of web3 lib
# 2. 
#
# ASSUMPTIONS:
# 
# 1. reserve0 is always DAI
# 

ETH_SWAP_AMOUNT = web3.toWei(1, 'ether')
ETH_BLOCK_TIME = 15

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

# TODO: for more precision do not use floats for price
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

# O(n!)
min_alloc = 2 # TODO: make this work with 1
def get_max_out(amount_in, token_in, pools, alloc=100):
    if (not pools or len(pools) <= 0): 
        return (0, ())
    if (amount_in <= 0 or alloc <= 0):
        return (0, (0,))

    max_out = 0
    allocations = ()
    pool = pools[0]
    for allocation in range(0, alloc + min_alloc, min_alloc):
        eth, dai = pool_data[pool]["eth"], pool_data[pool]["dai"]
        reserves = (eth, dai) if token_in == "eth" else (dai, eth)
        pool_in = math.floor(amount_in * allocation / 100)
        pool_out = get_amount_out(pool_in, *reserves)

        others_out, others_allocs = (
            get_max_out(amount_in, token_in, pools[1:], alloc - allocation))

        if max_out < pool_out + others_out:
            max_out = pool_out + others_out
            allocations = (pool_in,) + others_allocs

    return (max_out, allocations)


# O(k*n) -> O(log(k)n)
min_alloc2 = 2 # percent allocation out of 100
def get_max_out2(amount_in, token_in, pools=pool_data):
    """
    TODO optimization: start by allocating entirely to best
    priced pool then start rebalancing one allocation at a time
    then incremental increase in allocation fidelity

    returns a tuple of (amount_out, (allocations))
    where amount_out is the # of tokens recieved
    and allocations is a tuple with the number of tokens to go to each pool    

    O(n log(n) + k log(n))
    k = number of allocations (usually 100 - 1000)
    n = number of pools (usually 1 - 100)
    """

    alloc_amount = math.floor(amount_in * min_alloc2 / 100)

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
    
    pools = json.loads(json.dumps(pools))
    for pool in pools.keys():
        if not "allocation" in pools[pool]:
            pools[pool]["allocation"] = 0

    # allocate all the allocations
    for i in range(0, 100, min_alloc2):
        max_out, max_pool = pop_pool_heap()
        pools[max_pool]["allocation"] += min_alloc2
        
        # update the reserves
        if token_in == "eth":
            pools[max_pool]["eth"] += alloc_amount
            pools[max_pool]["dai"] -= max_out
        else:
            pools[max_pool]["eth"] -= max_out 
            pools[max_pool]["dai"] += alloc_amount
        
        push_pool_heap(max_pool)

    # # format allocs
    # def token_alloc(pool_key):
    #     return math.floor(amount_in * pools[pool_key]["allocation"] / 100)

    # get the total output
    def get_pool_output(pool_key):
        amount = math.floor(amount_in * pools[pool_key]["allocation"] / 100)
        return get_amount_out_dex(amount, token_in, pool_key)
    max_out = sum(map(get_pool_output, pools.keys()))

    # remove pools that don't cover their own extra gas cost
    active_pools = {k:v for k,v in pools.items() if v["allocation"] > 0}
    # sorted_pools = list(active_pools.keys())
    # sorted_pools.sort(
    #     key=lambda pool: active_pools[pool]["allocation"],
    #     reverse=True
    # )
    # for pool in sorted_pools:
    #     pool_amount = active_pools[pool]["allocation"]
    #     without_pool = {k:v for k,v in active_pools.items() if k != pool}
    #     rebalanced_out, new_pools = get_max_out2(pool_amount, token_in, without_pool)

    #     token_diff = max_out - rebalanced_out
    #     if token == "eth":
    #         token_diff = get_amount_out_dex(token_diff, "dai", "UniswapV2")
    #     if token_diff < swap_gas_fee:
    #         active_pools = new_pools # drop the pool

    return (max_out, active_pools)

def get_max_out3():
    """
    optimal balance achieved once no 2 pools
    can be rebalanced to increase token output

    1. give the pool with the best execution for the whole amount 100% allocation
    2. add another pool if it has a better price execution for min_alloc
    3. take the worst performing pool and try rebalancing with each other pool
       rebalancing done via binary search or min_alloc increments
    4. repeat 2-3 for each pool
    5. increase search fidelity (min_alloc) and repeat 2-4
       limit search range for allocations to +/- prev_min_alloc 

    make max heap of pool total execution price with alloc > 0 = O(n log(n))
    traverse allocation step sizes = O(log(k))
        make max heap of pool alloc execution output = O(n log(n))
        rebalance pool with worst total execution price 
            until no rebalance increases output = O(k log(n))

    = O(n log(n) log(k)) + O(k log(k) log(n))

    for each pool calculate the difference between including it and not
    and remove if the savings doesn't cover the gas cost = O(n^2 n log(n) n log(k))
    """


# @profile
def find_arbitrage():
    """
    returns (profit_in_usd, report_string)

    finds best arbitrage opportunity by getting the 
    highest eth bid quote and lowest eth ask quote
    """

    # max_out, allocs = get_max_out(ETH_SWAP_AMOUNT, "eth", list(pools.keys()))
    # logger.debug(f"""
    #     BRUTE FORCE ALGO
    #     tokens_out: {max_out}
    #     {tuple(pools.keys())}
    #     {allocs}
    # """)

    dai_out, eth_swaps = get_max_out2(ETH_SWAP_AMOUNT, "eth")
    eth_back, dai_swaps = get_max_out2(dai_out, "dai")
    logger.debug(f"""
        BALANCED DISTRIBUTION ALGO
        tokens_out: {dai_out}
        { json.dumps(
            { k:v["allocation"] for k,v in eth_swaps.items() }
        )}
        { list(eth_swaps.keys()) }
        { list(v["allocation"] for v in eth_swaps.values()) }
    """)

    # calculate profitability
    swap_count = len(list(filter(None,eth_swaps))) + len(list(filter(None,dai_swaps)))
    gas_fee = swap_gas_fee * swap_count
    profit = eth_back - ETH_SWAP_AMOUNT - gas_fee
    profit = web3.fromWei(abs(profit), 'ether') * -1 if profit < 0 else 1
    print(f"\t profit: {profit} eth \n")

    # dex with the highest eth price for eth2dai
    max_eth2dai = list(pools.keys())[0]
    for pool,price in pool_data.items():
        if price["eth2dai"] > pool_data[max_eth2dai]["eth2dai"]:
            max_eth2dai = pool

    # dex with the lowest eth price for dai2eth
    min_dai2eth = list(pools.keys())[0]
    for pool,price in pool_data.items():
        if price["dai2eth"] < pool_data[min_dai2eth]["dai2eth"]:
            min_dai2eth = pool

    return (0, 0)
        

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
        logger.info(f"""
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
        # calc_fees()
        gather_data()
        profit, report = find_arbitrage()

        if profit > max_profit:
            max_profit = profit
            logger.info(report)

        time.sleep(ETH_BLOCK_TIME)
main()