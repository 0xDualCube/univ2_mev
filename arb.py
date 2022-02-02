import logging
import json
from provider import web3

from decimal import Decimal
import math

from contracts import addresses, abis

logger = logging.getLogger()
logger.setLevel("DEBUG")

# ASSUMPTIONS:
# 
# 1. reserve0 is always DAI
# 

ETH_SWAP_AMOUNT = web3.toWei(0.0001, 'ether')

# init
def DexPool(address):
    return web3.eth.contract(
        address=web3.toChecksumAddress(address), 
        abi=abis.uniswap_abi
    )

pools = {
    k: DexPool(v) for k,v in addresses["dex"].items()
}

pool_prices = {
    k: {} for k,v in addresses["dex"].items()
}
def gather_data():
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