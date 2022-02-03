import logging
import json
from provider import web3
from functools import reduce
import time
from decimal import Decimal
import math
from contracts import addresses, abis
import requests

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
        # TODO handle negative outs
        # TODO make sure all the allocations accurately add up to amount_in

        others_out, others_allocs = (
            get_max_out(amount_in, token_in, pools[1:], alloc - allocation))

        if max_out < pool_out + others_out:
            max_out = pool_out + others_out
            allocations = (pool_in,) + others_allocs

    return (max_out, allocations)


# O(n)
min_alloc2 = 1 # percent allocation out of 100
def get_max_out2(amount_in, token_in):
    """
    optimal balance achieved once no 2 pools
    can be rebalanced to increase token output

    TODO optimization: start by allocating entirely to best
    priced pool then start rebalancing one allocation at a time

    returns a tuple of (amount_out, (allocations))
    where amount_out is the # of tokens recieved
    and allocations is a tuple of the number of tokens to go to each pool
    """

    pools = json.loads(json.dumps(pool_data))
    for pool in pools.keys():
        pools[pool]["allocation"] = 0

    alloc_amount = math.floor(amount_in * min_alloc2 / 100)

    for i in range(0, 100, min_alloc2):
        # get the pool that gets the best rate
        # O(n) -- can be reduced to O(log(n)) and usually O(1)
        max_pool = list(pools.keys())[0]
        max_out = 0
        for pool in pools.keys():
            amount_out = get_amount_out_dex(alloc_amount, token_in, pool, pools)
            if amount_out > max_out:
                max_out = amount_out
                max_pool = pool
        
        # update the reserves
        pools[max_pool]["allocation"] += min_alloc2
        if token_in == "eth":
            pools[max_pool]["eth"] += alloc_amount
            pools[max_pool]["dai"] -= max_out
        else:
            pools[max_pool]["eth"] -= max_out 
            pools[max_pool]["dai"] += alloc_amount

    # TODO remove smaller pool allocations where the savings don't cover gas fees 

    # format allocs
    def token_alloc(pool_key):
        return math.floor(amount_in * pools[pool_key]["allocation"] / 100)
    allocs = tuple(map(token_alloc, pools.keys()))
    
    # get the total output
    def calc_output(pool_key):
        amount = token_alloc(pool_key)
        return get_amount_out_dex(amount, token_in, pool_key)
    max_out = sum(map(calc_output, pools.keys()))

    return (max_out, allocs)

# @profile
def find_arbitrage():
    """
    returns (profit_in_usd, report_string)

    finds best arbitrage opportunity by getting the 
    highest eth bid quote and lowest eth ask quote
    """

    max_out, allocs = get_max_out(ETH_SWAP_AMOUNT, "eth", list(pools.keys()))
    logger.debug(f"""
        BRUTE FORCE ALGO
        tokens_out: {max_out}
        {tuple(pools.keys())}
        {allocs}
    """)

    dai_out, eth_swaps = get_max_out2(ETH_SWAP_AMOUNT, "eth")
    eth_back, dai_swaps = get_max_out2(dai_out, "dai")

    logger.debug(f"""
        BALANCED DISTRIBUTION ALGO
        tokens_out: {dai_out}
        {tuple(pools.keys())}
        {eth_swaps}
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
        calc_fees()
        gather_data()
        profit, report = find_arbitrage()

        if profit > max_profit:
            max_profit = profit
            logger.info(report)

        time.sleep(ETH_BLOCK_TIME)
main()