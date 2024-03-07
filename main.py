import asyncio, json, yaml, time, os

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



async def main():
        
        for i in config["coins"]:
            aevo.rest_cancel_all_orders("PERPETUAL", i)
            coin = aevo.get_markets(
                asset=i,
                instrument_type="PERPETUAL"
            )
            config["coins"][i]["price_precision"] = len(coin[0]["price_step"].split(".")[1]) if coin[0]["price_step"] != "1" else 0
            config["coins"][i]["size_precision"] = len(coin[0]["amount_step"].split(".")[1]) if coin[0]["amount_step"] != "1" else 0
            config["coins"][i]["tp_order"] = ""
            config["coins"][i]["positions"] = 0
            

        await init()

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
                        # old orders cancellation
                        aevo.rest_cancel_all_orders("PERPETUAL", i["asset"])

                        config["coins"][i["asset"]]["positions"] = 0

                        # construct the grid
                        is_buy = True if config["coins"][i["asset"]]["side"] == "LONG" else False
                        first_grid_step = config["coins"][i["asset"]]["first_grid_step"]
                        p_1 = float(i["mark_price"]) * (1 - first_grid_step/100) if config["coins"][i["asset"]]["side"] == "LONG" else float(i["mark_price"]) * (1 + first_grid_step/100)
                        p_2 = float(i["mark_price"])
                        s_1 = round(config["coins"][i["asset"]]["size"],config["coins"][i["asset"]]["size_precision"])
                        await aevo.create_order(
                                instrument_id = int(i["instrument_id"]), 
                                is_buy = is_buy, 
                                limit_price = p_1, 
                                quantity = s_1,
                                post_only = False)

                        for n in range(1, config["coins"][i["asset"]]["grids"]):
                            # create grid orders
                            p_n = p_1 - (p_2 - p_1) * config["coins"][i["asset"]]["grid_step"] if config["coins"][i["asset"]]["side"] == "LONG" else p_1 + (p_1 - p_2) * config["coins"][i["asset"]]["grid_step"]
                            price = round(p_n, config["coins"][i["asset"]]["price_precision"])
                            s_n = round(s_1 * (config["coins"][i["asset"]]["order_step"]),config["coins"][i["asset"]]["size_precision"])
                            await aevo.create_order(
                                instrument_id = int(i["instrument_id"]), 
                                is_buy = is_buy, 
                                limit_price = price, 
                                quantity = s_n,
                                post_only = False)
                            p_2 = p_1
                            p_1 = p_n
                            s_1 = s_n

                        # create market order
                        price = round(float(i["mark_price"]) * 1.05, config["coins"][i["asset"]]["price_precision"]) if is_buy else  0
                        await aevo.create_order(
                            instrument_id = int(i["instrument_id"]), 
                            is_buy = is_buy, 
                            limit_price = price, 
                            quantity = round(config["coins"][i["asset"]]["size"], config["coins"][i["asset"]]["size_precision"]),
                            post_only = False
                            )
                        await asyncio.sleep(0.5)
                        
                    else:
                        if float(i["amount"]) > config["coins"][i["asset"]]["positions"]:
                            logger.info(f'Filled grid order on {i["asset"]}')
                            # rebuid the TP
                            if config["coins"][i["asset"]]["tp_order"]:
                                # deleting old TP
                                await aevo.cancel_order(config["coins"][i["asset"]]["tp_order"])
                            # new TP price computing
                            if i["side"] == "buy":
                                is_buy = False
                                price = round(float(i["avg_entry_price"]) * (1 + config["coins"][i["asset"]]["take_step"]), config["coins"][i["asset"]]["price_precision"])
                            else:
                                is_buy = True
                                price = round(float(i["avg_entry_price"]) * (1 - config["coins"][i["asset"]]["take_step"]), config["coins"][i["asset"]]["price_precision"])

                            # new TP execution
                            config["coins"][i["asset"]]["tp_order"] = await aevo.create_order(
                                instrument_id=i["instrument_id"], 
                                is_buy=is_buy, 
                                limit_price=price,
                                post_only=False, 
                                quantity=round(float(i["amount"]),config["coins"][i["asset"]]["size_precision"]))
                            
                            config["coins"][i["asset"]]["positions"] = round(float(i["amount"]),config["coins"][i["asset"]]["size_precision"])
                            

async def init():
    await aevo.open_connection()
    logger.info("Positions subscribing ...")
    await aevo.subscribe_positions()

if __name__ == "__main__":
    asyncio.run(main())