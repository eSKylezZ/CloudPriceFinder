# scripts/fetch_aws.py
"""
Fetch AWS EC2 instance pricing and specs.
This is a placeholder script. Implement data fetching logic here.
"""

import json

def fetch_aws_data():
    # TODO: Implement actual fetching logic
    data = []
    # Example: data.append({"instanceType": "t3.micro", "vCPU": 2, "memoryGiB": 1, "priceUSD": 0.0104, ...})
    return data

if __name__ == "__main__":
    aws_data = fetch_aws_data()
    with open("data/aws.json", "w") as f:
        json.dump(aws_data, f, indent=2)