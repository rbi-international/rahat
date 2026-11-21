import os

# Every test runs offline on the deterministic fake lane. No key, no network, no cost.
os.environ["PROVIDER"] = "fake"
os.environ["FALLBACK_PROVIDER"] = ""
os.environ["ADMIN_TOKEN"] = "test-admin"
# Pin it so a developer's .env (load_dotenv fills unset vars) cannot lock /metrics during tests.
os.environ["METRICS_TOKEN"] = ""
os.environ["SOS_COOLDOWN_S"] = "0"
os.environ["SOS_MAX_PER_DEVICE"] = "50"
