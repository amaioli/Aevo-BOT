# Welcome to Aevo-BOT!

With Aevo-BOT you can perform a grid martingala strategy on Aevo Perpetual Marketplace.



#### Installation

- Clone the repository locally: git clone https://github.com/amaioli/Aevo-BOT.git

- Install python requirements: pip3 install -r requirements.txt
- Launch Aevo-BOT: python3 main.py

- Initialization:
At the first start the bot will ask you a wallet private key and the ethereum wallet address. 

#### Configuration
Aevo-BOT configuration is managed inside config.yaml. When config.yaml is changed you've to restart the bot to make changes effectives.

(example with field explanation)

    DYM:
            first_grid_step: 0.5 # % distance of the first grid step from the marketprice
            grid_step: 1.5 # distance multiplier for each grid step 
            grids: 4 # number of grid steps
            order_step: 1.5 # size multiplier for each step
            side: LONG # side of the position LONG or SHORT
            size: 10 # first order size
            take_step: 0.5 # % distance of the take step from position average price
