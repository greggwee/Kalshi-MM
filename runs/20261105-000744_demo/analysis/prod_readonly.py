"""Read-only check of pmm_prod_config.yaml against production market data.

Uses the unsigned public client, so it holds no credentials and cannot place an
order whatever the code above it does, and wraps it in DryRunGateway as well.
Run artifacts go to the scratchpad."""
import json
import logging
import os
import sys

WT = r"C:\Users\grego\AppData\Local\Temp\claude\840c593829146935\8552379b-feeb-4475-830b-aaa408586bd7\scratchpad\wt"
OUT = r"C:\Users\grego\AppData\Local\Temp\claude\840c593829146935\8552379b-feeb-4475-830b-aaa408586bd7\scratchpad\prod_ro"
sys.path.insert(0, WT)
os.makedirs(OUT, exist_ok=True)
import yaml
from src.clients import Environment
from src.credentials import make_public_client
from src.diagnostics import write_report
from src.gateway import DryRunGateway, ExchangeGateway
from src.journal import Journal
from src.selector import format_selection, select_markets, session_specs
from src.session import TradingSession

duration = float(sys.argv[1]) if len(sys.argv) > 1 else 300.0
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    handlers=[logging.FileHandler(os.path.join(OUT, "session.log"), encoding="utf-8"),
                              logging.StreamHandler()])
logger = logging.getLogger("prod_ro")
with open(os.path.join(WT, "pmm_prod_config.yaml"), encoding="utf-8") as f:
    config = yaml.safe_load(f)
config["strategy"]["pmm"]["calibration_file"] = os.path.join(WT, "pmm_calibration.json")

client = make_public_client(Environment.PROD)
assert client.private_key is None
base = ExchangeGateway(client)
selection = select_markets(base, config["selection"], logger)
print(format_selection(selection), flush=True)
print("scanned", selection.get("scanned"), "eligible", selection.get("eligible"), "rejected",
      selection.get("rejected"), flush=True)
specs = session_specs(selection)
if not specs:
    sys.exit("nothing selected")
journal = Journal(os.path.join(OUT, "events.jsonl"))
session = TradingSession(DryRunGateway(base), specs, config, journal, logger, dry_run=True)
session.run(duration)
journal.close()
report = write_report(OUT)
for f in report["findings"][:10]:
    print(f"  {f['severity'].upper():8s} {f['area']}: {f['text']}")
