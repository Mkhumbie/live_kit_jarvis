"""Project tools module.

This file provides an `OutlookClient` helper to manage Outlook (Office 365) mailboxes
via Microsoft Graph using client-credentials (app) authentication.

Environment variables expected (in `.env`):
- OUTLOOK_CLIENT_ID
- OUTLOOK_CLIENT_SECRET
- OUTLOOK_TENANT_ID
- OUTLOOK_USER        # userPrincipalName / email for the mailbox to operate on

Notes:
- This implementation uses application permissions (client credentials). The
  app must be granted Mail.ReadWrite (or the narrower permission set) in Azure
  AD by an admin. The client credentials flow cannot call `/me`; we call
  `/users/{OUTLOOK_USER}` instead.
"""

from typing import List, Optional, Dict, Any
import os
import logging
import requests
from dotenv import load_dotenv

# Prefer MSAL when available; fall back to direct token request if not installed
try:
    import msal  # type: ignore
    _MSAL_AVAILABLE = True
except Exception:
    msal = None  # type: ignore
    _MSAL_AVAILABLE = False

load_dotenv()

logger = logging.getLogger(__name__)


class OutlookClient:
    """Simple Outlook/Graph client for mailbox operations.

    Example usage:
        client = OutlookClient()
        msgs = client.list_messages(limit=5)
        client.send_message(subject="Hi", body="Hello", to_recipients=["foo@bar.com"])
    """

    SCOPE = ["https://graph.microsoft.com/.default"]

    def __init__(self, client_id: Optional[str] = None, client_secret: Optional[str] = None,
                 tenant_id: Optional[str] = None, user: Optional[str] = None):
        self.client_id = client_id or os.getenv("OUTLOOK_CLIENT_ID")
        self.client_secret = client_secret or os.getenv("OUTLOOK_CLIENT_SECRET")
        self.tenant_id = tenant_id or os.getenv("OUTLOOK_TENANT_ID")
        self.user = user or os.getenv("OUTLOOK_USER")

        if not all([self.client_id, self.client_secret, self.tenant_id, self.user]):
            raise ValueError("OUTLOOK_CLIENT_ID, OUTLOOK_CLIENT_SECRET, OUTLOOK_TENANT_ID and OUTLOOK_USER must be set in environment")

        self.authority = f"https://login.microsoftonline.com/{self.tenant_id}"
        self._msal_app = None
        if _MSAL_AVAILABLE:
            self._msal_app = msal.ConfidentialClientApplication(
                self.client_id, authority=self.authority, client_credential=self.client_secret
            )

        token = self._get_token()
        if not token:
            raise RuntimeError("Failed to obtain access token for Microsoft Graph")

        self.base_url = f"https://graph.microsoft.com/v1.0/users/{self.user}"
        self._session = requests.Session()
        self._session.headers.update({"Authorization": f"Bearer {token}", "Content-Type": "application/json"})

    def _get_token(self) -> Optional[str]:
        # If MSAL is available, prefer it (handles caching, retries, etc.)
        if _MSAL_AVAILABLE and self._msal_app is not None:
            try:
                result = self._msal_app.acquire_token_silent(self.SCOPE, account=None)
            except Exception:
                result = None

            if not result:
                result = self._msal_app.acquire_token_for_client(scopes=self.SCOPE)

            if "access_token" in (result or {}):
                return result["access_token"]
            logger.error("MSAL token acquisition failed: %s", result)

        # Fallback: perform a direct client-credentials POST to the v2 token endpoint
        # Note: this avoids requiring `msal` but does not implement caching.
        token_url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
        payload = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": "https://graph.microsoft.com/.default",
        }
        try:
            r = requests.post(token_url, data=payload, timeout=10)
            r.raise_for_status()
            data = r.json()
            if "access_token" in data:
                return data["access_token"]
            logger.error("Token response missing access_token: %s", data)
        except Exception as e:
            logger.exception("Direct token request failed: %s", e)
        return None

    def list_messages(self, limit: int = 20) -> List[Dict[str, Any]]:
        """List messages from the user's inbox. Returns list of message dicts."""
        url = f"{self.base_url}/mailFolders/Inbox/messages?$top={limit}"
        r = self._session.get(url)
        r.raise_for_status()
        data = r.json()
        return data.get("value", [])

    def get_message(self, message_id: str) -> Dict[str, Any]:
        url = f"{self.base_url}/messages/{message_id}"
        r = self._session.get(url)
        r.raise_for_status()
        return r.json()

    def send_message(self, subject: str, body: str, to_recipients: List[str]) -> Dict[str, Any]:
        """Send a message from the mailbox. `to_recipients` is a list of email addresses."""
        url = f"{self.base_url}/sendMail"
        payload = {
            "message": {
                "subject": subject,
                "body": {"contentType": "Text", "content": body},
                "toRecipients": [{"emailAddress": {"address": r}} for r in to_recipients],
            },
            "saveToSentItems": "true",
        }
        r = self._session.post(url, json=payload)
        r.raise_for_status()
        # sendMail returns 202 No Content on success; return status
        return {"status_code": r.status_code}

    def mark_as_read(self, message_id: str) -> Dict[str, Any]:
        url = f"{self.base_url}/messages/{message_id}"
        payload = {"isRead": True}
        r = self._session.patch(url, json=payload)
        r.raise_for_status()
        return r.json()

    def delete_message(self, message_id: str) -> bool:
        url = f"{self.base_url}/messages/{message_id}"
        r = self._session.delete(url)
        if r.status_code in (204, 200):
            return True
        r.raise_for_status()
        return False

    def search_messages(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Search messages with Microsoft Graph query parameter `search`.

        Example query: 'subject:"Invoice"' or 'from:alice@contoso.com'
        Requires appropriate permissions for message search.
        """
        # Graph supports `$search` only on advanced queries; ensure query is quoted
        url = f"{self.base_url}/messages?$search=\"{query}\"&$top={limit}"
        r = self._session.get(url)
        r.raise_for_status()
        return r.json().get("value", [])


__all__ = ["OutlookClient", "get_weather", "search_web", "read_emails", "send_email"]

import logging
from livekit.agents import function_tool, RunContext
import requests
from langchain_community.tools import DuckDuckGoSearchRun
import os
import smtplib
from email.mime.multipart import MIMEMultipart  
from email.mime.text import MIMEText
from typing import Optional

@function_tool()
async def get_weather(
    context: RunContext,  # type: ignore
    city: str) -> str:
    """
    Get the current weather for a given city.
    Logs all operations and confirms task completion.
    """
    logging.info("[TASK START] get_weather: Retrieving weather for city: %s", city)
    try:
        response = requests.get(
            f"https://wttr.in/{city}?format=3")
        if response.status_code == 200:
            weather_data = response.text.strip()
            logging.info("[TASK COMPLETE] get_weather: Successfully retrieved weather for %s: %s", city, weather_data)
            # Return confirmation message with weather data
            confirmation = f"Weather for {city}: {weather_data}"
            return confirmation
        else:
            logging.error("[TASK FAILED] get_weather: Failed to retrieve weather for %s (HTTP %d)", city, response.status_code)
            return f"Could not retrieve weather for {city}."
    except Exception as e:
        logging.error("[TASK FAILED] get_weather: Exception retrieving weather for %s: %s", city, e)
        return f"An error occurred while retrieving weather for {city}." 

@function_tool()
async def search_web(
    context: RunContext,  # type: ignore
    query: str) -> str:
    """
    Search the web using DuckDuckGo.
    Logs search queries and results, confirms task completion.
    """
    logging.info("[TASK START] search_web: Searching for query: %s", query)
    try:
        results = DuckDuckGoSearchRun().run(tool_input=query)
        logging.info("[TASK COMPLETE] search_web: Found results for query '%s'", query)
        # Add confirmation to results
        confirmation = f"Search completed for '{query}'. Here are the results:\n{results}"
        return confirmation
    except Exception as e:
        logging.error("[TASK FAILED] search_web: Exception searching for '%s': %s", query, e)
        return f"An error occurred while searching the web for '{query}'."    


@function_tool()
async def read_emails(
    context: RunContext,  # type: ignore
    limit: int = 10
) -> str:
    """List recent emails from the configured mailbox.

    Returns a short JSON-like string with id, subject, from and isRead flag.
    Logs all operations and confirms task completion.
    """
    logging.info("[TASK START] read_emails: Retrieving up to %d emails", limit)
    try:
        # Initialize Outlook client - this will handle token refresh
        client = OutlookClient()
        logging.debug("OutlookClient initialized successfully")
    except Exception as e:
        logging.exception("[TASK FAILED] read_emails: Failed to create OutlookClient: %s", e)
        return f"Error initializing Outlook client: {e}"

    try:
        # Retrieve messages from mailbox
        msgs = client.list_messages(limit=limit)
        out_lines = []
        for m in msgs:
            out_lines.append({
                "id": m.get("id"),
                "subject": m.get("subject"),
                "from": (m.get("from") or {}).get("emailAddress", {}).get("address"),
                "isRead": m.get("isRead"),
            })
        logging.info("[TASK COMPLETE] read_emails: Successfully retrieved %d messages from mailbox", len(out_lines))
        # Return formatted result with confirmation
        result = f"Email retrieval complete. Retrieved {len(out_lines)} messages:\n{str(out_lines)}"
        return result
    except Exception as e:
        logging.exception("[TASK FAILED] read_emails: Exception listing messages: %s", e)
        return f"Error listing messages: {e}"


@function_tool()
async def send_email(
    context: RunContext,  # type: ignore
    to_recipients: str,
    subject: str,
    body: str,
) -> str:
    """Send an email from the configured mailbox.

    `to_recipients` can be a comma-separated string of email addresses.
    Logs all send operations and confirms task completion.
    """
    logging.info("[TASK START] send_email: Sending email to recipients: %s with subject: %s", to_recipients, subject)
    try:
        # Initialize Outlook client - will handle token refresh
        client = OutlookClient()
        logging.debug("OutlookClient initialized for send operation")
    except Exception as e:
        logging.exception("[TASK FAILED] send_email: Failed to create OutlookClient: %s", e)
        return f"Error initializing Outlook client: {e}"

    # Parse comma-separated recipients
    recipients = [r.strip() for r in to_recipients.split(",") if r.strip()]
    if not recipients:
        logging.warning("[TASK FAILED] send_email: No valid recipients provided")
        return "No recipients provided."

    try:
        # Send the message via Graph API
        resp = client.send_message(subject=subject, body=body, to_recipients=recipients)
        logging.info("[TASK COMPLETE] send_email: Successfully sent email to recipients: %s (Status: %d)", 
                     ", ".join(recipients), resp.get('status_code', 'unknown'))
        # Return confirmation message
        confirmation = f"Email sent successfully to {', '.join(recipients)}. Subject: {subject}"
        return confirmation
    except Exception as e:
        logging.exception("[TASK FAILED] send_email: Exception sending message to %s: %s", to_recipients, e)
        return f"Error sending message: {e}"


# `search_and_send_email` removed per user request. Use `search_web` + `send_email` instead.

