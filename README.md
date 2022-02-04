# UniswapV2 MEV Searcher
This bot does some simple mev detection on uniswap v2 pools

Note that it is very unikely to find any MEV, even with a low swap setting 
because it takes gas cost into account. If you would like to expiriment with
zero cast cost, comment out the calc_fees() from main().

Also note that it depends on ethgasstation.info being up and returning correct results

## How to run

    $ python arb.py
