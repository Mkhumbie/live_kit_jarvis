"""Example runner for `OutlookClient`.

Set these environment variables in `.env` before running:

OUTLOOK_CLIENT_ID=...
OUTLOOK_CLIENT_SECRET=...
OUTLOOK_TENANT_ID=...
OUTLOOK_USER=someone@contoso.com

Run:
    python outlook_tool_example.py

This script will list the top 5 messages in the inbox. Sending is commented
out and can be enabled once you confirm the recipient and environment.
"""

import os
from pprint import pprint

from tools import OutlookClient


def main():
    required = ["OUTLOOK_CLIENT_ID", "OUTLOOK_CLIENT_SECRET", "OUTLOOK_TENANT_ID", "OUTLOOK_USER"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        print("Missing environment variables:", ", ".join(missing))
        print("Please populate these in your .env file and re-run. Example:")
        print("\nOUTLOOK_CLIENT_ID=your-client-id\nOUTLOOK_CLIENT_SECRET=your-secret\nOUTLOOK_TENANT_ID=your-tenant-id\nOUTLOOK_USER=you@contoso.com\n")
        return

    client = OutlookClient()
    print("Listing top 5 messages in Inbox:")
    msgs = client.list_messages(limit=5)
    print(f"Found {len(msgs)} messages")
    for m in msgs:
        subj = m.get("subject")
        mid = m.get("id")
        frm = (m.get("from") or {}).get("emailAddress", {}).get("address")
        print(f"- ID:{mid} From:{frm} Subject:{subj}")

    # Example: send a message (disabled by default)
    # Uncomment and change recipient before running
    # resp = client.send_message(subject="Test from LiveKit Jarvis", body="Hello from example script", to_recipients=["recipient@contoso.com"])
    # pprint(resp)


if __name__ == "__main__":
    main()
