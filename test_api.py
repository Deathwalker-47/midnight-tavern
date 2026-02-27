import urllib.request
import json

url = "https://api.anthropic.com/v1/messages"
headers = {
    "Content-Type": "application/json",
    "x-api-key": "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",
    "anthropic-version": "2023-06-01"
}
data = json.dumps({
    "model": "claude-sonnet-4-5-20250929",
    "max_tokens": 50,
    "messages": [{"role": "user", "content": "Say hi"}]
}).encode()

try:
    req = urllib.request.Request(url, data=data, headers=headers)
    resp = urllib.request.urlopen(req, timeout=20)
    print("SUCCESS:", resp.read().decode()[:200])
except Exception as e:
    print("ERROR:", str(e))
