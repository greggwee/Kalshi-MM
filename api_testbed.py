from dotenv import load_dotenv
import asyncio

from src.clients import KalshiHttpClient, KalshiWebSocketClient, Environment
from src.credentials import load_credentials

# Load environment variables
load_dotenv()
env = Environment.DEMO # toggle environment here
KEYID, private_key = load_credentials(env)

# Initialize the HTTP client
client = KalshiHttpClient(
    key_id=KEYID,
    private_key=private_key,
    environment=env
)

# Get account balance
balance = client.get_balance()
print("Balance:", balance)

# Get orders
orders = client.get_orders()
print("Order:", orders)

# Initialize the WebSocket client
ws_client = KalshiWebSocketClient(
    key_id=KEYID,
    private_key=private_key,
    environment=env
)

# # Connect via WebSocket
# asyncio.run(ws_client.connect())

market = client.get_market("KXNCAAF-26-ORE")
print(market)
