import logging
import json
from provider import web3

from decimal import Decimal
import math

from contracts import addresses, abis

logger = logging.getLogger()
logger.setLevel("DEBUG")

uniswapV2 = web3.eth.contract(
    address=web3.toChecksumAddress(addresses["dex"]["UniswapV2"]), 
    abi=abis.uniswap_abi
)

reserve0, reserve1, *_ = uniswapV2.functions.getReserves().call()
logging.info(f"reserve0: {reserve0}, reserve1: {reserve1}")
    