from web3 import Web3
import os

api_key = os.environ.get("ALCHEMY_API_KEY")
if not api_key:
    raise Exception("you must set the ALCHEMY_API_KEY env variable")

rpc = f"https://eth-mainnet.alchemyapi.io/v2/{api_key}"
web3 = Web3(Web3.HTTPProvider(rpc))
eth = web3.eth