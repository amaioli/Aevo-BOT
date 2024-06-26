import asyncio, json, yaml, os, sys, time

import logging as logger

from aevo import AevoClient


logger.basicConfig(
        level=os.getenv("LOGGING_LEVEL", "INFO"),
        format="%(asctime)s.%(msecs)03d | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


with open('config.yaml', 'r') as file:
    config = yaml.safe_load(file)
    if not(config["config"]["signing_key_private"]):
        # first launch
        aevo =  AevoClient(env = config["config"]["env"])
        if not(aevo.signing_key_private):
            exit()
    else: 
        aevo = AevoClient(
        signing_key_private = config["config"]["signing_key_private"],
        wallet_address = config["config"]["wallet_address"],
        api_key = config["config"]["api_key"],
        api_secret = config["config"]["api_secret"],
        env = config["config"]["env"],
)
        
with open('config.yaml', 'w') as file:
    config["config"]["signing_key_private"] = aevo.signing_key_private
    config["config"]["wallet_address"] = aevo.wallet_address
    config["config"]["api_key"] = aevo.api_key
    config["config"]["api_secret"] = aevo.api_secret
    yaml.dump(config, file)

async def refresh_config_loop():
    global config
    while True:
        with open('config.yaml', 'r') as file:
            config = yaml.safe_load(file)
            await asyncio.sleep(10)



async def main():
        
        await init()

        #await refresh_config_loop()
        
        for i in config["coins"]:
            aevo.rest_cancel_all_orders("PERPETUAL", i)
            coin = aevo.get_markets(
                asset=i,
                instrument_type="PERPETUAL"
            )
            config["coins"][i]["price_precision"] = len(coin[0]["price_step"].split(".")[1]) if coin[0]["price_step"] != "1" else 0
            config["coins"][i]["size_precision"] = len(coin[0]["amount_step"].split(".")[1]) if coin[0]["amount_step"] != "1" else 0
            config["coins"][i]["instrument_id"] = coin[0]["instrument_id"]
            config["coins"][i]["min_order_value"] = coin[0]["min_order_value"]
            config["coins"][i]["tp_order"] = ""
            config["coins"][i]["positions"] = 0

            # if len(sys.argv) > 3 and sys.argv[1] == "grid_create":
            await create_grid(asset=i, market_price=coin[0]["mark_price"])


        async for msg in aevo.read_messages(on_disconnect=init):
            msg_json = json.loads(msg)
            try:
                msg_json["data"]["positions"][0]
            except:
                continue

            for i in msg_json["data"]["positions"]:
                if i["asset"] in config["coins"] and i["instrument_type"] == "PERPETUAL":
                    if float(i["amount"]) == 0:
                        logger.info(f'take TP {i["asset"]}')

                        await create_grid(asset=i["asset"], market_price=i["mark_price"])
                        
                    else:
                        if (float(i["amount"]) - config["coins"][i["asset"]]["positions"]) * float(i["mark_price"]) > float(config["coins"][i["asset"]]["min_order_value"]):
                            logger.info(f'Filled grid order on {i["asset"]}')
                            # rebuid the TP
                            if config["coins"][i["asset"]]["tp_order"]:
                                # deleting old TP
                                await aevo.cancel_order(config["coins"][i["asset"]]["tp_order"])
                            # new TP price computing
                            if i["side"] == "buy":
                                is_buy = False
                                price = round(float(i["avg_entry_price"]) * (1 + config["coins"][i["asset"]]["take_step"]/100), config["coins"][i["asset"]]["price_precision"])
                            else:
                                is_buy = True
                                price = round(float(i["avg_entry_price"]) * (1 - config["coins"][i["asset"]]["take_step"]/100), config["coins"][i["asset"]]["price_precision"])

                            # new TP execution
                            config["coins"][i["asset"]]["tp_order"] = aevo.rest_create_order(
                                instrument_id=i["instrument_id"], 
                                is_buy=is_buy, 
                                limit_price=price,
                                post_only=False, 
                                quantity=round(float(i["amount"]),config["coins"][i["asset"]]["size_precision"]))
                            logger.info(f'Created TP order with id: {config["coins"][i["asset"]]["tp_order"]}')
                            config["coins"][i["asset"]]["positions"] = round(float(i["amount"]),config["coins"][i["asset"]]["size_precision"])
                            

async def create_grid(asset, market_price):
    aevo.rest_cancel_all_orders("PERPETUAL", asset=asset)

    config["coins"][asset]["positions"] = 0

    instrument_id = int(config["coins"][asset]["instrument_id"])
    is_buy = True if config["coins"][asset]["side"] == "LONG" else False
    first_grid_step = config["coins"][asset]["first_grid_step"]
    p_1 = float(market_price) * (1 - first_grid_step/100) if config["coins"][asset]["side"] == "LONG" else float(market_price) * (1 + first_grid_step/100)
    p_1 = round(p_1, config["coins"][asset]["price_precision"])
    p_2 = float(market_price)
    p_2 = round(p_2, config["coins"][asset]["price_precision"])
    s_1 = round(config["coins"][asset]["size"],config["coins"][asset]["size_precision"])
    aevo.rest_create_order(
            instrument_id = instrument_id, 
            is_buy = is_buy, 
            limit_price = p_1, 
            quantity = s_1,
            post_only = False)

    # create grid orders
    for n in range(1, config["coins"][asset]["grids"]):
        
        p_n = p_1 - (p_2 - p_1) * config["coins"][asset]["grid_step"] if config["coins"][asset]["side"] == "LONG" else p_1 + (p_1 - p_2) * config["coins"][asset]["grid_step"]
        price = round(p_n, config["coins"][asset]["price_precision"])
        s_n = round(s_1 * (config["coins"][asset]["order_step"]),config["coins"][asset]["size_precision"])
        aevo.rest_create_order(
            instrument_id = instrument_id, 
            is_buy = is_buy, 
            limit_price = price, 
            quantity = s_n,
            post_only = False)
        p_2 = p_1
        p_1 = p_n
        s_1 = s_n

    # create market order
    price = round(float(market_price) * 1.05, config["coins"][asset]["price_precision"]) if is_buy else  0
    aevo.rest_create_order(
        instrument_id = instrument_id, 
        is_buy = is_buy, 
        limit_price = price, 
        quantity = round(config["coins"][asset]["size"], config["coins"][asset]["size_precision"]),
        post_only = False
        )

async def init():
    time.sleep(5)
    await aevo.open_connection()
    logger.info("Positions subscribing ...")
    await aevo.subscribe_positions()

if __name__ == "__main__":
    # Python version checking
    if sys.version_info[0] < 3 and sys.version_info[1] < 9 and sys.version_info[2] < 18:
        logger.debug('Python version should be at least 3.1.18')
        exit()
    asyncio.run(main())
