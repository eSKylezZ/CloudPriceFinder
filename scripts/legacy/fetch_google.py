# scripts/fetch_google.py
"""
Fetch Google Cloud instance pricing and specs.
This is a placeholder script. Implement data fetching logic here.
"""

import json

def fetch_google_data():
    # TODO: Implement actual fetching logic
    data = []
    # Example: data.append({"instanceType": "e2-micro", "vCPU": 2, "memoryGiB": 1, "priceUSD": 0.0076, ...})
    return data

if __name__ == "__main__":
    google_data = fetch_google_data()
    with open("data/google.json", "w") as f:
        json.dump(google_data, f, indent=2)