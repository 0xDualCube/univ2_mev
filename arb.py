import logging
import json
from provider import web3
from functools import reduce

from decimal import Decimal
import math

from contracts import addresses, abis

logger = logging.getLogger()
logger.setLevel("DEBUG")

# ASSUMPTIONS:
# 
# 1. reserve0 is always DAI
# 

ETH_SWAP_AMOUNT = web3.toWei(0.001, 'ether')

# init contracts 
def DexPool(address):
    return web3.eth.contract(
        address=web3.toChecksumAddress(address), 
        abi=abis.uniswap_abi
    )
pools = { k: DexPool(v) for k,v in addresses["dex"].items() }

# TODO: for more precision do not use floats for price
pool_prices = { k: {} for k,v in addresses["dex"].items() }
def gather_data():
    """
    gathers eth bid and ask quotes from all the dexes
    """

    for dex,pool in pools.items():
        dai_reserve, eth_reserve, *_ = pool.functions.getReserves().call()
        
        eth2dai_amount = get_amount_out(ETH_SWAP_AMOUNT, eth_reserve, dai_reserve)
        eth2dai_price = eth2dai_amount / ETH_SWAP_AMOUNT
        pool_prices[dex]["eth2dai"] = eth2dai_price
        
        dai2eth_amount = get_amount_in(ETH_SWAP_AMOUNT, dai_reserve, eth_reserve)
        dai2eth_price = dai2eth_amount / ETH_SWAP_AMOUNT
        pool_prices[dex]["dai2eth"] = dai2eth_price

        print(f"""
            {dex}
            dai_reserve: {dai_reserve}
            eth_reserve: {eth_reserve}
            eth2dai_price: {eth2dai_price}
            dai2eth_price: {dai2eth_price}
        """)

def find_arbitrage():
    """
    finds best arbitrage opportunity by getting the 
    highest eth bid quote and lowest eth ask quote
    """

    # dex with the highest eth price for eth2dai
    max_eth2dai = "UniswapV2"
    for pool,price in pool_prices.items():
        if price["eth2dai"] <= 0: continue
        if price["eth2dai"] > pool_prices[max_eth2dai]["eth2dai"]:
            max_eth2dai = pool

    # dex with the lowest eth price for dai2eth
    min_dai2eth = "UniswapV2"
    for pool,price in pool_prices.items():
        if price["dai2eth"] <= 0: continue
        if price["dai2eth"] < pool_prices[min_dai2eth]["dai2eth"]:
            min_dai2eth = pool

    profit = web3.fromWei(ETH_SWAP_AMOUNT, 'ether') * Decimal(
        pool_prices[max_eth2dai]["eth2dai"]
        - pool_prices[min_dai2eth]["dai2eth"]
    )

    if profit <= 0: return
    
    eth_in = web3.fromWei(ETH_SWAP_AMOUNT, 'ether')
    dai_out = Decimal(pool_prices[max_eth2dai]["eth2dai"]) * eth_in
    eth_out = dai_out / Decimal(pool_prices[min_dai2eth]["dai2eth"])
    print(f"""
        arbitrage from {max_eth2dai} to {min_dai2eth}
        1. trade {eth_in} eth for {dai_out} dai on {max_eth2dai}
        2. trade {dai_out} dai for {eth_out} eth on {min_dai2eth}
        3. profit {eth_out - eth_in} eth (${profit})
    """)
        

def get_amount_out(amount, reserve_in, reserve_out):
    """
    gets the token amount out given reserve levels and input amount

    X * Y = K
    Xold Yold = Xnew Ynew
    ΔY = (Y ΔX)/(X + ΔX)
    
    ref: https://github.com/Uniswap/v2-periphery/blob/master/contracts/libraries/UniswapV2Library.sol
    """

    amount_in_with_fee = amount * 997
    numerator = amount_in_with_fee * reserve_out
    denominator = (reserve_in * 1000) + amount_in_with_fee
    amount_out = numerator // denominator
    return amount_out

def get_amount_in(amount, reserve_in, reserve_out):
    """
    gets the token amount in required to get the amount out
    
    ref: https://github.com/Uniswap/v2-periphery/blob/master/contracts/libraries/UniswapV2Library.sol
    """

    numerator = reserve_in * amount * 1000
    denominator = (reserve_out - amount) * 997
    amount_in = (numerator // denominator) + 1
    return amount_in

gather_data()
find_arbitrage()