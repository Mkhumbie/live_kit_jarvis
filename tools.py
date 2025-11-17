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
import json

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
        # Create the message first so we can capture its ID and metadata,
        # then send it. This allows returning a webLink / internetMessageId
        # for better confirmation to the user.
        create_url = f"{self.base_url}/messages"
        payload = {
            "subject": subject,
            "body": {"contentType": "Text", "content": body},
            "toRecipients": [{"emailAddress": {"address": r}} for r in to_recipients],
        }
        # Create message (draft)
        r = self._session.post(create_url, json=payload)
        # Log response for debugging (status and truncated body)
        try:
            logger.debug("Outlook create response status=%s body=%s", r.status_code, (r.text or '')[:1000])
        except Exception:
            pass
        r.raise_for_status()
        msg = r.json()
        msg_id = msg.get("id")

        if not msg_id:
            return {"status_code": r.status_code, "error": "failed_to_create_message"}

        # Send the created message
        send_url = f"{self.base_url}/messages/{msg_id}/send"
        r2 = self._session.post(send_url)
        # Log send response for debugging
        try:
            logger.debug("Outlook send response status=%s text=%s", r2.status_code, (r2.text or '')[:1000])
        except Exception:
            pass
        # send returns 202 on success
        # After sending, fetch the message by id to return useful metadata
        try:
            get_url = f"{self.base_url}/messages/{msg_id}"
            r3 = self._session.get(get_url)
            try:
                logger.debug("Outlook get message status=%s body=%s", r3.status_code, (r3.text or '')[:1000])
            except Exception:
                pass
            r3.raise_for_status()
            sent_msg = r3.json()
        except Exception as e:
            logger.exception("Failed to fetch sent message metadata: %s", e)
            sent_msg = {}

        return {
            "status_code": r2.status_code,
            "message_id": msg_id,
            "webLink": sent_msg.get("webLink"),
            "internetMessageId": sent_msg.get("internetMessageId"),
        }

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
import asyncio

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

    Returns a human-readable numbered list of emails with subject, sender, and read status.
    
    IMPORTANT: The assistant MUST present exactly what this tool returns.
    Do not make up or hallucinate email content - use only what this tool returns.
    """
    print("==== [TOOL ENTRY] read_emails function called ====")
    logging.info("==== [TOOL ENTRY] read_emails function called with limit=%d ====", limit)
    logging.info("[TASK START] read_emails: Retrieving up to %d emails", limit)
    try:
        # Initialize Outlook client - this will handle token refresh
        client = OutlookClient()
        logging.debug("OutlookClient initialized successfully")
    except Exception as e:
        logging.exception("[TASK FAILED] read_emails: Failed to create OutlookClient: %s", e)
        return f"Error initializing Outlook client: {e}"

    try:
        # Retrieve messages from mailbox using $select for predictable fields including body
        url = f"{client.base_url}/mailFolders/Inbox/messages?$select=id,subject,from,isRead,receivedDateTime,body&$top={limit}"
        r = client._session.get(url)
        try:
            logging.debug("read_emails: raw response status=%s body=%s", r.status_code, (r.text or '')[:2000])
        except Exception:
            pass
        r.raise_for_status()
        data = r.json()
        msgs = data.get("value", [])

        out_lines = []
        for m in msgs:
            # Extract body content
            body_data = m.get("body") or {}
            content = body_data.get("content", "")
            # Clean up HTML content if present
            if body_data.get("contentType") == "html":
                import re
                # Basic HTML stripping
                content = re.sub(r'<[^>]+>', '', content)
                content = re.sub(r'\s+', ' ', content).strip()
            
            out_lines.append({
                "id": m.get("id"),
                "subject": m.get("subject"),
                "from": (m.get("from") or {}).get("emailAddress", {}).get("address"),
                "isRead": m.get("isRead"),
                "receivedDateTime": m.get("receivedDateTime"),
                "content": content[:200] + "..." if len(content) > 200 else content,  # Truncate long content
            })

        logging.info("[TASK COMPLETE] read_emails: Successfully retrieved %d messages from mailbox", len(out_lines))
        
        # Build a human-readable summary for the assistant to speak/present
        if len(out_lines) == 0:
            summary = "TOOL_OUTPUT_START: No emails found in your inbox. TOOL_OUTPUT_END"
        else:
            summary_lines = [f"TOOL_OUTPUT_START: Retrieved {len(out_lines)} email(s) from your actual mailbox:"]
            for idx, msg in enumerate(out_lines, 1):
                subj = msg.get('subject', '(no subject)')
                sender = msg.get('from', '(unknown)')
                read_status = "read" if msg.get('isRead') else "unread"
                content = msg.get('content', '')
                
                if content:
                    summary_lines.append(f"{idx}. {subj} — from {sender} ({read_status})")
                    summary_lines.append(f"   Content: {content}")
                else:
                    summary_lines.append(f"{idx}. {subj} — from {sender} ({read_status}) [No content preview]")
                summary_lines.append("")  # Add blank line between emails
            if len(out_lines) > 10:
                summary_lines.append(f"... and {len(out_lines)-10} more.")
            summary_lines.append("TOOL_OUTPUT_END")
            summary = "\n".join(summary_lines)
        
        # Force a small delay to ensure complete execution
        import asyncio
        await asyncio.sleep(0.1)
        
        # Return ONLY the plain text summary so the assistant presents it directly
        # Do not wrap in JSON since the LLM isn't parsing it correctly
        logging.info("read_emails: RETURNING TO AGENT: %s", summary)
        print(f"==== [TOOL RETURN] read_emails tool returning: {summary} ====")
        print(f"==== [TOOL EXIT] read_emails function exiting successfully ====")
        
        # AGGRESSIVE DEBUGGING: Make sure tool result is visible
        logging.critical("===== TOOL RESULT CRITICAL LOG =====")
        logging.critical("read_emails returning: %s", summary)
        logging.critical("===== END TOOL RESULT =====")
        
        # WORKAROUND: Write to a file so we can verify the tool executed  
        try:
            from datetime import datetime
            with open("debug_tool_result.txt", "w", encoding="utf-8") as f:
                f.write(f"TOOL EXECUTED AT: {datetime.now()}\n")
                f.write(f"RESULT: {summary}\n")
        except Exception as e:
            logging.debug("Could not write debug file: %s", e)
        
        # Ensure the result is a proper string
        final_result = str(summary)
        print(f"==== [FINAL RESULT] Type: {type(final_result)}, Content: {final_result} ====")
        return final_result
    except Exception as e:
        logging.exception("[TASK FAILED] read_emails: Exception listing messages: %s", e)
        return f"Error listing messages: {e}"


@function_tool()
async def read_email_content(
    context: RunContext,  # type: ignore
    email_subject: str
) -> str:
    """Read the full content of a specific email by subject line.
    
    Args:
        email_subject: The subject line or part of the subject line of the email to read
        
    Returns:
        Full email content including subject, sender, and body text
    """
    logging.info("[TASK START] read_email_content: Looking for email with subject containing '%s'", email_subject)
    
    try:
        # Initialize Outlook client
        client = OutlookClient()
        logging.debug("OutlookClient initialized successfully")
    except Exception as e:
        logging.exception("[TASK FAILED] read_email_content: Failed to create OutlookClient: %s", e)
        return f"Error initializing Outlook client: {e}"

    try:
        # Search for emails with matching subject
        # Use $filter to search for emails containing the subject text
        encoded_subject = email_subject.replace("'", "''")  # Escape single quotes for OData
        url = f"{client.base_url}/mailFolders/Inbox/messages?$filter=contains(subject,'{encoded_subject}')&$select=id,subject,from,body,receivedDateTime&$top=5"
        
        r = client._session.get(url)
        r.raise_for_status()
        data = r.json()
        msgs = data.get("value", [])
        
        if not msgs:
            return f"TOOL_OUTPUT_START: No emails found with subject containing '{email_subject}'. TOOL_OUTPUT_END"
        
        # Get the first matching email
        email = msgs[0]
        subject = email.get("subject", "(no subject)")
        sender = (email.get("from") or {}).get("emailAddress", {}).get("address", "(unknown)")
        received_date = email.get("receivedDateTime", "")
        
        # Extract full body content
        body_data = email.get("body") or {}
        content = body_data.get("content", "")
        content_type = body_data.get("contentType", "")
        
        # Clean up HTML content if present
        if content_type == "html":
            import re
            # More thorough HTML cleanup
            content = re.sub(r'<style[^>]*>.*?</style>', '', content, flags=re.DOTALL)
            content = re.sub(r'<script[^>]*>.*?</script>', '', content, flags=re.DOTALL)
            content = re.sub(r'<[^>]+>', '', content)
            content = re.sub(r'&nbsp;', ' ', content)
            content = re.sub(r'&lt;', '<', content)
            content = re.sub(r'&gt;', '>', content)
            content = re.sub(r'&amp;', '&', content)
            content = re.sub(r'\s+', ' ', content).strip()
        
        # Format the response
        result = f"""TOOL_OUTPUT_START: Email Content:

Subject: {subject}
From: {sender}
Received: {received_date}

Content:
{content}

TOOL_OUTPUT_END"""
        
        logging.info("[TASK COMPLETE] read_email_content: Successfully retrieved email content")
        return result
        
    except Exception as e:
        logging.exception("[TASK FAILED] read_email_content: Exception reading email content: %s", e)
        return f"Error reading email content: {e}"


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
    # Use a thread to run the blocking OutlookClient network calls so we don't block
    # the agent's asyncio event loop. Also add a timeout to avoid hanging tasks.
    try:
        # Parse comma-separated recipients
        recipients = [r.strip() for r in to_recipients.split(",") if r.strip()]
        if not recipients:
            logging.warning("[TASK FAILED] send_email: No valid recipients provided")
            return "No recipients provided."

        def _send_sync(recipients_list, subj, body_text):
            # Create OutlookClient inside the thread; MSAL will cache tokens automatically
            client = OutlookClient()
            logging.debug("OutlookClient initialized in thread for send")
            return client.send_message(subject=subj, body=body_text, to_recipients=recipients_list)

        logging.info("[TASK START] send_email: dispatching send in thread for %s", ", ".join(recipients))
        try:
            # wait up to 15 seconds for the send to complete; adjust as needed
            resp = await asyncio.wait_for(asyncio.to_thread(_send_sync, recipients, subject, body), timeout=15.0)
        except asyncio.TimeoutError:
            logging.exception("[TASK FAILED] send_email: send operation timed out")
            return "Error: send operation timed out"

        status = resp.get('status_code', 'unknown')
        logging.info("[TASK COMPLETE] send_email: Successfully sent email to recipients: %s (Status: %s)", ", ".join(recipients), status)

        parts = [f"Email send attempted to {', '.join(recipients)}.", f"Subject: {subject}", f"Status: {status}"]
        if resp.get('message_id'):
            parts.append(f"message_id: {resp.get('message_id')}")
        if resp.get('internetMessageId'):
            parts.append(f"internetMessageId: {resp.get('internetMessageId')}")
        if resp.get('webLink'):
            parts.append(f"webLink: {resp.get('webLink')}")

        confirmation = " ".join(parts)
        return confirmation
    except Exception as e:
        logging.exception("[TASK FAILED] send_email: Exception sending message to %s: %s", to_recipients, e)
        return f"Error sending message: {e}"


@function_tool()
async def test_simple_tool(context: RunContext) -> str:
    """Simple test tool to verify if tool results are properly handled by the agent.
    
    Returns predictable output that should be easy to identify if properly used.
    """
    result = "SIMPLE_TOOL_OUTPUT: This is a test from test_simple_tool. The current time is 2:30 PM."
    print(f"==== [TEST SIMPLE TOOL] Returning: {result} ====")
    logging.info("test_simple_tool returning: %s", result)
    return result


# `search_and_send_email` removed per user request. Use `search_web` + `send_email` instead.

