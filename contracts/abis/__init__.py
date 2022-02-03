import json
import os

# TODO: pull in all the .abi files in the dir

unipair_filepath = os.path.join(
    os.path.dirname(__file__), './IUniswapV2Pair.abi'
)

unirouter_filepath = os.path.join(
    os.path.dirname(__file__), './UniswapV2Router02.abi'
)

abis = {
    "IUniswapV2Pair": json.load(open(unipair_filepath)),
    "UniswapV2Router02": json.load(open(unirouter_filepath))
}
