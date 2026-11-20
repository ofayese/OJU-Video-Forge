import hashlib
import hmac
import json
import sys

sys.path.insert(0, "/opt/forge")

raw = open("/opt/forge/telemetry/observation/observation.json", "rb").read()
key = open("/run/secrets/telemetry-hmac.key", "rb").read().strip()
env = json.loads(raw)

from app.host_telemetry import canonical_payload

cb = canonical_payload(env["payload"])
print("sig_match_canonical:",
      hmac.compare_digest(hmac.new(key, cb, hashlib.sha256).hexdigest(),
                          env["signature"]))
print("payload_seq:", env["payload"]["sequence"])
