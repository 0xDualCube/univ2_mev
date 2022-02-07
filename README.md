# UniswapV2 MEV Searcher
This bot does some simple mev detection on uniswap v2 pools

Note that it is very unikely to find any MEV, even with a low swap setting 
because it takes gas cost into account. If you would like to experiment with
zero gas cost, comment out calc_fees() inside main().

Also note that it depends on ethgasstation.info being up and returning correct results

## How to run

    $ python arb.py
