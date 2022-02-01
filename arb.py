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
# 1. token0 is alaways DAI
# 


# init
def DexPool(address):
    return web3.eth.contract(
        address=web3.toChecksumAddress(address), 
        abi=abis.uniswap_abi
    )

pools = {
    k: DexPool(v) for k,v in addresses["dex"].items()
}



def gatherData():
    for dex,pool in pools.items():
    reserve0, reserve1, *_ = pool.functions.getReserves().call()
    print(f"dex {dex}: \t reserve0: {reserve0} \t reserve1: {reserve1}")
    