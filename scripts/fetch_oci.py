# scripts/fetch_oci.py
"""
Fetch Oracle Cloud (OCI) instance pricing and specs.
This is a placeholder script. Implement data fetching logic here.
"""

import json

def fetch_oci_data():
    # TODO: Implement actual fetching logic
    data = []
    # Example: data.append({"instanceType": "VM.Standard.E2.1.Micro", "vCPU": 1, "memoryGiB": 1, "priceUSD": 0.0, ...})
    return data

if __name__ == "__main__":
    oci_data = fetch_oci_data()
    with open("data/oci.json", "w") as f:
        json.dump(oci_data, f, indent=2)