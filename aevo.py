import asyncio
import json
import random
import time
import traceback
import logging as logger
from eth_account.messages import encode_structured_data
from http import HTTPStatus

import requests
import websockets
from eth_account import Account
from eth_hash.auto import keccak

#from logger import logger
#from web3 import Web3

from eip712_structs import Address, Boolean, EIP712Struct, Uint, make_domain

CONFIG = {
    "testnet": {
        "rest_url": "https://api-testnet.aevo.xyz",
        "ws_url": "wss://ws-testnet.aevo.xyz",
        "signing_domain": {
            "name": "Aevo Testnet",
            "version": "1",
            "chainId": "11155111",
        },
    },
    "mainnet": {
        "rest_url": "https://api.aevo.xyz",
        "ws_url": "wss://ws.aevo.xyz",
        "signing_domain": {
            "name": "Aevo Mainnet",
            "version": "1",
            "chainId": "1",
        },
    },
}


class Order(EIP712Struct):
    maker = Address()
    isBuy = Boolean()
    limitPrice = Uint(256)
    amount = Uint(256)
    salt = Uint(256)
    instrument = Uint(256)
    timestamp = Uint(256)


class AevoClient:
    def __init__(
        self,
        signing_key_private="",
        wallet_address="",
        api_key="",
        api_secret="",
        env="testnet",
        rest_headers={},
    ):
        self.signing_key_private = signing_key_private
        self.wallet_address = wallet_address
        self.api_key = api_key
        self.api_secret = api_secret
        self.connection = None
        self.client = requests
        self.rest_headers = {
            "AEVO-KEY": api_key,
            "AEVO-SECRET": api_secret,
        }
        self.extra_headers = None
        self.rest_headers.update(rest_headers)

        if (env != "testnet") and (env != "mainnet"):
            raise ValueError("env must either be 'testnet' or 'mainnet'")
        self.env = env

        if not(self.signing_key_private):
            # collect wallet informations
            private_key_wallet = input("Please type your private key (private key will not be stored): ")
            address_wallet = input("Please type your wallet address: ")
            # generate perpetual keys
            perpetual_key = self.perpetual_key_generation(private_key_wallet=private_key_wallet, address_wallet=address_wallet)

            #perpetual keys loading
            response = self.rest_register(
                account=perpetual_key['account'], 
                signing_key=perpetual_key['signing_key'],
                account_signature=perpetual_key['account_signature'],
                signing_key_signature=perpetual_key['signing_key_signature'])
            
            
            try:
                response["success"]
                self.signing_key_private = perpetual_key['signing_key_private']
                self.wallet_address = perpetual_key['account']
                self.api_key = response['api_key']
                self.api_secret = response['api_secret']
                self.rest_headers = {
                    "AEVO-KEY": self.api_key,
                    "AEVO-SECRET": self.api_secret,
                    }
                self.extra_headers = None
                self.rest_headers.update(rest_headers)
            except:
                self.signing_key_private = ""
                logger.error("Problem during account creation on Aevo platform!")
    

    @property
    def address(self):
        return Account.from_key(self.signing_key_private).address

    @property
    def rest_url(self):
        return CONFIG[self.env]["rest_url"]

    @property
    def ws_url(self):
        return CONFIG[self.env]["ws_url"]

    @property
    def signing_domain(self):
        return CONFIG[self.env]["signing_domain"]
    
    def perpetual_key_generation(self, private_key_wallet, address_wallet):
        # Create a random wallet
        private_key_random = Account.create().privateKey

        # Create an account object from the private key
        signer = Account.privateKeyToAccount(private_key_random)
        signing_key = signer.address

        expiry = 4133920928000000000 #31/12/2100

        # Define the TypedData structure
        typed_data = {
            "types": {
                "EIP712Domain": [
                    {"name": "name", "type": "string"},
                    {"name": "version", "type": "string"},
                    {"name": "chainId", "type": "uint256"},
                ],
                "Register": [
                    {"name": "key", "type": "address"},
                    {"name": "expiry", "type": "uint256"}
                ]
            },
            "primaryType": "Register",
            "domain": {
                "name": "Aevo Mainnet",
                "version": "1",
                "chainId": 1
            },
            "message": {
                "key": signing_key,  
                "expiry": expiry  
            }
        }

        # Encode the TypedData hash
        register_hash = encode_structured_data(typed_data)

        # Sign the hash
        account_signature = Account.sign_message(register_hash, private_key_wallet)  


        typed_data = {
            "types": {
                "EIP712Domain": [
                    {"name": "name", "type": "string"},
                    {"name": "version", "type": "string"},
                    {"name": "chainId", "type": "uint256"},
                ],
                "SignKey": [
                    {"name": "account", "type": "address"}
                ]
            },
            "primaryType": "SignKey",
            "domain": {
                "name": "Aevo Mainnet",
                "version": "1",
                "chainId": 1
            },
            "message": {
                "account": address_wallet 
            }
        }

        # Encode the TypedData hash
        signing_key_signature = Account.sign_message(encode_structured_data(typed_data), private_key_random)

        return {
            'account': address_wallet, 
            'signing_key': signer.address,
            'signing_key_private': private_key_random.hex(),
            'expiry': str(expiry),
            'account_signature': account_signature.signature.hex(),
            'signing_key_signature': signing_key_signature.signature.hex()}
    

    async def open_connection(self, extra_headers={}):
        try:
            logger.info("Opening Aevo websocket connection...")

            self.connection = await websockets.connect(
                self.ws_url, ping_interval=20, extra_headers=extra_headers
            )
            if not self.extra_headers:
                self.extra_headers = extra_headers

            if self.api_key and self.wallet_address:
                logger.debug(f"Connecting to {self.ws_url}...")
                await self.connection.send(
                    json.dumps(
                        {
                            "id": 1,
                            "op": "auth",
                            "data": {
                                "key": self.api_key,
                                "secret": self.api_secret,
                            },
                        }
                    )
                )

            # Sleep as authentication takes some time, especially slower on testnet
            await asyncio.sleep(1)
        except Exception as e:
            logger.error("Error thrown when opening connection")
            logger.error(e)
            logger.error(traceback.format_exc())
            await asyncio.sleep(10)  # Don't retry straight away

    async def reconnect(self):
        logger.info("Trying to reconnect Aevo websocket...")
        await self.close_connection()
        await self.open_connection(self.extra_headers)

    async def close_connection(self):
        try:
            logger.info("Closing connection...")
            await self.connection.close()
            logger.info("Connection closed")
        except Exception as e:
            logger.error("Error thrown when closing connection")
            logger.error(e)
            logger.error(traceback.format_exc())

    async def read_messages(self, read_timeout=1, backoff=0.1, on_disconnect=None):
        while True:
            try:
                message = await asyncio.wait_for(
                    self.connection.recv(), timeout=read_timeout
                )
                yield message
            except (
                websockets.exceptions.ConnectionClosedError,
                websockets.exceptions.ConnectionClosedOK,
            ) as e:
                logger.error("Aevo websocket connection close")
                if on_disconnect:
                    await on_disconnect()
                # logger.error(e)
                # logger.error(traceback.format_exc())
                # await self.reconnect()
            except asyncio.TimeoutError:
                await asyncio.sleep(backoff)
            except Exception as e:
                logger.error(e)
                logger.error(traceback.format_exc())
                await asyncio.sleep(1)

    async def send(self, data):
        try:
            await self.connection.send(data)
        except websockets.exceptions.ConnectionClosedError as e:
            logger.debug("Restarted Aevo websocket connection")
            await self.reconnect()
            await self.connection.send(data)
        except:
            await self.reconnect()

    # Public REST API
    def get_index(self, asset):
        req = self.client.get(f"{self.rest_url}/index?symbol={asset}")
        data = req.json()
        return data

    def get_markets(self, asset, instrument_type):
        req = self.client.get(f"{self.rest_url}/markets?asset={asset}&instrument_type={instrument_type}")
        data = req.json()
        return data

    # Private REST API
    def rest_register(self, account, signing_key, account_signature, signing_key_signature):

        payload = {
            "account": account,
            "signing_key": signing_key,
            "expiry": "4133920928000000000",
            "account_signature": account_signature,
            "signing_key_signature": signing_key_signature,
            "referral_code": "Pumped-Plaid-Loeb"
        }
        headers = {
            "accept": "application/json",
            "content-type": "application/json"
        }

        req = self.client.post(
            f"{self.rest_url}/register", json=payload, headers=headers
        )
        try:
            return req.json()
        except:
            return req.text()
        
    def rest_create_order(
        self, instrument_id, is_buy, limit_price, quantity, post_only=True
    ):
        data, order_id = self.create_order_rest_json(
            int(instrument_id), is_buy, limit_price, quantity, post_only
        )
        logger.info(data)
        req = self.client.post(
            f"{self.rest_url}/orders", json=data, headers=self.rest_headers
        )
        if req.status_code == HTTPStatus.OK:
            return order_id
        else:
            error = req.text
            logger.error(f'Problem with order creation: {error}')

    def rest_create_market_order(self, instrument_id, is_buy, quantity):
        limit_price = 0
        if is_buy:
            limit_price = 2**256 - 1

        data, order_id = self.create_order_rest_json(
            int(instrument_id),
            is_buy,
            limit_price,
            quantity,
            decimals=1,
            post_only=False,
        )
        logger.info(data)
        req = self.client.post(
            f"{self.rest_url}/orders", json=data, headers=self.rest_headers
        )
        if req.status_code == HTTPStatus.OK:
                return order_id
        else:
            error = req.text
            logger.error(f'Problem with order creation: {error}')

    def rest_cancel_order(self, order_id):
        req = self.client.delete(
            f"{self.rest_url}/orders/{order_id}", headers=self.rest_headers
        )
        logger.info(req.json())
        return req.json()

    def rest_get_cancel_on_disconnect(self):
        req = self.client.get(f"{self.rest_url}/account/cancel-on-disconnect", headers=self.rest_headers)
        return req.json()

    def rest_get_account(self):
        req = self.client.get(f"{self.rest_url}/account", headers=self.rest_headers)
        return req.json()

    def rest_get_portfolio(self):
        req = self.client.get(f"{self.rest_url}/portfolio", headers=self.rest_headers)
        return req.json()

    def rest_get_open_orders(self):
        req = self.client.get(
            f"{self.rest_url}/orders", json={}, headers=self.rest_headers
        )
        return req.json()

    def rest_cancel_all_orders(
        self,
        instrument_type=None,
        asset=None,
    ):
        body = {}
        if instrument_type:
            body["instrument_type"] = instrument_type

        if asset:
            body["asset"] = asset

        req = self.client.delete(
            f"{self.rest_url}/orders-all", json=body, headers=self.rest_headers
        )
        return req.json()

    # Public WS Subscriptions
    async def subscribe_tickers(self, asset):
        await self.send(
            json.dumps(
                {
                    "op": "subscribe",
                    "data": [f"ticker:{asset}:OPTION"],
                }
            )
        )

    async def subscribe_ticker(self, channel):
        msg = json.dumps(
            {
                "op": "subscribe",
                "data": [channel],
            }
        )
        await self.send(msg)

    async def subscribe_markprice(self, asset):
        await self.send(
            json.dumps(
                {
                    "op": "subscribe",
                    "data": [f"markprice:{asset}:OPTION"],
                }
            )
        )

    async def subscribe_orderbook(self, instrument_name):
        await self.send(
            json.dumps(
                {
                    "op": "subscribe",
                    "data": [f"orderbook:{instrument_name}"],
                }
            )
        )

    async def subscribe_trades(self, instrument_name):
        await self.send(
            json.dumps(
                {
                    "op": "subscribe",
                    "data": [f"trades:{instrument_name}"],
                }
            )
        )

    async def subscribe_index(self, asset):
        await self.send(json.dumps({"op": "subscribe", "data": [f"index:{asset}"]}))

    # Private WS Subscriptions
    async def subscribe_orders(self):
        payload = {
            "op": "subscribe",
            "data": ["orders"],
        }
        await self.send(json.dumps(payload))

    async def subscribe_positions(self):
        payload = {
            "op": "subscribe",
            "data": ["positions"],
        }
        await self.send(json.dumps(payload))

    async def subscribe_fills(self):
        payload = {
            "op": "subscribe",
            "data": ["fills"],
        }
        await self.send(json.dumps(payload))

    # Private WS Commands
    def create_order_ws_json(
        self,
        instrument_id,
        is_buy,
        limit_price,
        quantity,
        post_only=True,
        mmp=False,
        price_decimals=10**6,
        amount_decimals=10**6,
    ):
        timestamp = int(time.time())
        salt, signature, order_id = self.sign_order(
            instrument_id=instrument_id,
            is_buy=is_buy,
            limit_price=limit_price,
            quantity=quantity,
            timestamp=timestamp,
            price_decimals=price_decimals,
        )

        payload = {
            "instrument": instrument_id,
            "maker": self.wallet_address,
            "is_buy": is_buy,
            "amount": str(int(round(quantity * amount_decimals, is_buy))),
            "limit_price": str(int(round(limit_price * price_decimals, is_buy))),
            "salt": str(salt),
            "signature": signature,
            "post_only": post_only,
            "mmp": mmp,
            "timestamp": timestamp,
        }
        return payload, order_id

    def create_order_rest_json(
        self,
        instrument_id,
        is_buy,
        limit_price,
        quantity,
        post_only=True,
        reduce_only=False,
        close_position=False,
        price_decimals=10**6,
        amount_decimals=10**6,
        trigger=None,
        stop=None,
    ):
        timestamp = int(time.time())
        salt, signature, order_id = self.sign_order(
            instrument_id=instrument_id,
            is_buy=is_buy,
            limit_price=limit_price,
            quantity=quantity,
            timestamp=timestamp,
            price_decimals=price_decimals,
        )
        payload = {
            "maker": self.wallet_address,
            "is_buy": is_buy,
            "instrument": instrument_id,
            "limit_price": str(int(round(limit_price * price_decimals, is_buy))),
            "amount": str(int(round(quantity * amount_decimals, is_buy))),
            "salt": str(salt),
            "signature": signature,
            "post_only": post_only,
            "reduce_only": reduce_only,
            "close_position": close_position,
            "timestamp": timestamp,
        }
        if trigger and stop:
            payload["trigger"] = trigger
            payload["stop"] = stop

        return payload, order_id

    async def create_order(
        self,
        instrument_id,
        is_buy,
        limit_price,
        quantity,
        post_only=True,
        id=None,
        mmp=False,
    ):
        data, order_id = self.create_order_ws_json(
            instrument_id=int(instrument_id),
            is_buy=is_buy,
            limit_price=limit_price,
            quantity=quantity,
            post_only=post_only,
            mmp=mmp,
        )
        payload = {"op": "create_order", "data": data}
        if id:
            payload["id"] = id

        logger.info(payload)
        await self.send(json.dumps(payload))

        return order_id

    async def edit_order(
        self,
        order_id,
        instrument_id,
        is_buy,
        limit_price,
        quantity,
        id=None,
        post_only=True,
        mmp=True,
    ):
        timestamp = int(time.time())
        instrument_id = int(instrument_id)
        salt, signature, new_order_id = self.sign_order(
            instrument_id=instrument_id,
            is_buy=is_buy,
            limit_price=limit_price,
            quantity=quantity,
            timestamp=timestamp,
        )
        payload = {
            "op": "edit_order",
            "data": {
                "order_id": order_id,
                "instrument": instrument_id,
                "maker": self.wallet_address,
                "is_buy": is_buy,
                "amount": str(int(round(quantity * 10**6, is_buy))),
                "limit_price": str(int(round(limit_price * 10**6, is_buy))),
                "salt": str(salt),
                "signature": signature,
                "post_only": post_only,
                "mmp": mmp,
                "timestamp": timestamp,
            },
        }

        if id:
            payload["id"] = id

        logger.info(payload)
        await self.send(json.dumps(payload))

        return new_order_id

    async def cancel_order(self, order_id):
        if not order_id:
            return

        payload = {"op": "cancel_order", "data": {"order_id": order_id}}
        logger.info(payload)
        await self.send(json.dumps(payload))

    async def cancel_all_orders(self):
        payload = {"op": "cancel_all_orders", "data": {}}
        await self.send(json.dumps(payload))

    def sign_order(
        self,
        instrument_id,
        is_buy,
        limit_price,
        quantity,
        timestamp,
        price_decimals=10**6,
        amount_decimals=10**6,
    ):
        salt = random.randint(0, 10**10)  # We just need a large enough number

        order_struct = Order(
            maker=self.wallet_address,  # The wallet"s main address
            isBuy=is_buy,
            limitPrice=int(round(limit_price * price_decimals, is_buy)),
            amount=int(round(quantity * amount_decimals, is_buy)),
            salt=salt,
            instrument=instrument_id,
            timestamp=timestamp,
        )
        logger.info(self.signing_domain)
        domain = make_domain(**self.signing_domain)
        signable_bytes = keccak(order_struct.signable_bytes(domain=domain))
        return (
            salt,
            Account._sign_hash(signable_bytes, self.signing_key_private).signature.hex(),
            f"0x{signable_bytes.hex()}",
        )


async def main():
    # The following values which are used for authentication on private endpoints, can be retrieved from the Aevo UI
    aevo = AevoClient(
        signing_key="",
        wallet_address="",
        api_key="",
        api_secret="",
        env="testnet",
    )

    markets = aevo.get_markets("ETH")
    logger.info(markets)

    await aevo.open_connection()
    await aevo.subscribe_ticker("ticker:ETH:PERPETUAL")

    async for msg in aevo.read_messages():
        logger.info(msg)


if __name__ == "__main__":
    asyncio.run(main())
