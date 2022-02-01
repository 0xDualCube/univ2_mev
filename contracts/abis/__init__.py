import json
import os

# TODO: pull in all the .abi files in the dir
filepath = os.path.join(
    os.path.dirname(__file__), './IUniswapV2Pair.abi'
)

uniswap_abi = json.load(open(filepath))