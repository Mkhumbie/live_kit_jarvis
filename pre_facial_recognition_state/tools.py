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

Optimizations:
- Intelligent caching system with 5-minute TTL for calendar operations
- Background cache refresh to avoid LiveKit timeout issues
- Pre-warming cache mechanism for common limit values (5, 10)
- Shared helper functions for date parsing and HTML cleanup
- TOOL_OUTPUT markers for anti-hallucination measures
"""

from typing import List, Optional, Dict, Any
import os
import logging
import requests
from dotenv import load_dotenv
import json
import asyncio

# LiveKit imports for function tools
from livekit.agents import function_tool, RunContext

# DuckDuckGo search
from langchain_community.tools import DuckDuckGoSearchRun

# Helper functions
def _clean_html_content(content: str) -> str:
    """Clean HTML content and return plain text."""
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
    return content

# Prefer MSAL when available; fall back to direct token request if not installed
try:
    import msal  # type: ignore
    _MSAL_AVAILABLE = True
except Exception:
    msal = None  # type: ignore
    _MSAL_AVAILABLE = False

load_dotenv()

logger = logging.getLogger(__name__)


# outlook_client.py
import time
import logging
import requests
from typing import Any, Dict, List, Optional, Union
import os

# Optional MSAL (recommended). If not available, client will fallback to direct token POST.
try:
    import msal  # type: ignore
    _MSAL_AVAILABLE = True
except Exception:
    _MSAL_AVAILABLE = False

logger = logging.getLogger(__name__)


class OutlookClient:
    """
    Production-ready Microsoft Graph client optimized for realtime agents.

    - Uses MSAL (if available) for token caching / silent auth
    - Uses a persistent requests.Session for connection pooling
    - Centralized `_request()` wrapper with retries/backoff and token refresh
    - Returns only JSON-serializable Python types (dicts / lists / primitives)
    """

    DEFAULT_SCOPE = ["https://graph.microsoft.com/.default"]

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        tenant_id: Optional[str] = None,
        user: Optional[str] = None,
        timeout: int = 10,
        max_retries: int = 3,
    ):
        self.client_id = client_id or os.getenv("OUTLOOK_CLIENT_ID")
        self.client_secret = client_secret or os.getenv("OUTLOOK_CLIENT_SECRET")
        self.tenant_id = tenant_id or os.getenv("OUTLOOK_TENANT_ID")
        self.user = user or os.getenv("OUTLOOK_USER")

        if not all([self.client_id, self.client_secret, self.tenant_id, self.user]):
            raise ValueError(
                "OUTLOOK_CLIENT_ID, OUTLOOK_CLIENT_SECRET, OUTLOOK_TENANT_ID and OUTLOOK_USER must be set"
            )

        self.authority = f"https://login.microsoftonline.com/{self.tenant_id}"
        self.timeout = timeout
        self.max_retries = max_retries

        # MSAL app (if available)
        self._msal_app = None
        if _MSAL_AVAILABLE:
            try:
                self._msal_app = msal.ConfidentialClientApplication(
                    client_id=self.client_id,
                    client_credential=self.client_secret,
                    authority=self.authority,
                )
            except Exception as e:
                logger.warning("MSAL initialization failed: %s", e)
                self._msal_app = None

        # Shared session (connection pooling)
        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})
        self.base_url = f"https://graph.microsoft.com/v1.0/users/{self.user}"

        # Token cache fields (in-memory)
        self._cached_token: Optional[str] = None
        self._token_expiry: float = 0.0

    # -----------------------
    # Token handling
    # -----------------------
    def _get_token_msal(self) -> Optional[str]:
        """Try MSAL cached token first, otherwise request a new one via MSAL."""
        if not self._msal_app:
            return None

        try:
            result = self._msal_app.acquire_token_silent(self.DEFAULT_SCOPE, account=None)
        except Exception:
            result = None

        if not result:
            result = self._msal_app.acquire_token_for_client(scopes=self.DEFAULT_SCOPE)

        if isinstance(result, dict) and result.get("access_token"):
            # Note: msal returns 'expires_in' sometimes - but we rely on msal caching.
            return result["access_token"]

        logger.debug("MSAL token acquisition returned: %s", result)
        return None

    def _get_token_fallback(self) -> Optional[str]:
        """Fallback direct OAuth client_credentials POST to token endpoint (no advanced caching)."""
        token_url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
        payload = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": "https://graph.microsoft.com/.default",
        }
        try:
            r = requests.post(token_url, data=payload, timeout=self.timeout)
            r.raise_for_status()
            data = r.json()
            return data.get("access_token")
        except Exception as e:
            logger.exception("Direct token request failed: %s", e)
            return None

    def _get_token(self) -> str:
        """
        Return a usable access token. Prefer MSAL (with caching) when available,
        else fallback to direct token request. The method also caches token briefly in-memory.
        """
        now = time.time()
        # Use cached in-memory token if still valid
        if self._cached_token and now < self._token_expiry - 60:
            return self._cached_token

        # Prefer MSAL
        token = None
        if _MSAL_AVAILABLE and self._msal_app:
            token = self._get_token_msal()

        # If MSAL unavailable or failed, fallback to direct POST
        if not token:
            token = self._get_token_fallback()

        if not token:
            raise RuntimeError("Failed to acquire access token for Microsoft Graph")

        # Update in-memory cache with a conservative expiry (55 minutes)
        self._cached_token = token
        self._token_expiry = now + (55 * 60)
        return token

    # -----------------------
    # Request helper
    # -----------------------
    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self._get_token()}", "Content-Type": "application/json"}

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        timeout: Optional[Union[int, float]] = None,
    ) -> Dict[str, Any]:
        """
        Central request wrapper:
        - automatically refreshes token on 401
        - retries on transient errors (429, 5xx) with exponential backoff
        - raises on fatal errors
        Returns parsed JSON dict for successful responses, or raises.
        """
        timeout = timeout or self.timeout
        attempt = 0
        last_exc = None

        while attempt <= self.max_retries:
            try:
                resp = self._session.request(
                    method=method,
                    url=url,
                    headers=self._headers(),
                    params=params,
                    json=json,
                    timeout=timeout,
                )
                # Token might have been invalidated server-side
                if resp.status_code in (401, 403):
                    logger.info("Token invalid/expired (status=%s). Refreshing token and retrying.", resp.status_code)
                    # Clear cached token so _get_token forces renewal
                    self._cached_token = None
                    attempt += 1
                    time.sleep(0.5 * (2 ** attempt))
                    continue

                # Retry on throttling or transient server errors
                if resp.status_code in (429, 500, 502, 503, 504):
                    backoff = 0.5 * (2 ** attempt)
                    logger.warning("Transient error %s on %s %s - retry %d after %fs", resp.status_code, method, url, attempt, backoff)
                    time.sleep(backoff)
                    attempt += 1
                    continue

                resp.raise_for_status()
                # For 204 No Content or empty body, return empty dict
                if resp.status_code == 204 or not (resp.text and resp.text.strip()):
                    return {}

                try:
                    return resp.json()
                except ValueError:
                    # Not JSON
                    logger.error("Graph response is not JSON: %s", resp.text[:1000])
                    raise

            except requests.RequestException as e:
                last_exc = e
                if attempt >= self.max_retries:
                    logger.exception("OutlookClient request failed permanently: %s", e)
                    raise
                backoff = 0.5 * (2 ** attempt)
                logger.warning("Request exception: %s - retrying (attempt %d) after %fs", e, attempt, backoff)
                time.sleep(backoff)
                attempt += 1

        # If we reach here, raise the last exception
        if last_exc:
            raise last_exc
        raise RuntimeError("OutlookClient failed request unexpectedly")

    # -----------------------
    # High-level helpers
    # -----------------------
    def get_events(self, start_iso: str, end_iso: str, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get calendar events between ISO timestamps (UTC-like strings).
        Returns list of event dicts (plain JSON).
        """
        filter_query = f"start/dateTime ge '{start_iso}' and start/dateTime le '{end_iso}'"
        url = f"{self.base_url}/calendar/events"
        params = {
            "$filter": filter_query,
            "$select": "id,subject,start,end,attendees,location",
            "$orderby": "start/dateTime",
            "$top": limit,
        }
        data = self._request("GET", url, params=params)
        return data.get("value", [])

    def list_messages(self, limit: int = 20) -> List[Dict[str, Any]]:
        url = f"{self.base_url}/mailFolders/Inbox/messages"
        params = {"$top": limit}
        data = self._request("GET", url, params=params)
        return data.get("value", [])

    def get_message(self, message_id: str) -> Dict[str, Any]:
        url = f"{self.base_url}/messages/{message_id}"
        data = self._request("GET", url)
        return data

    def send_message(self, subject: str, body: str, to_recipients: List[str]) -> Dict[str, Any]:
        """
        Create + send an email. Returns summary dict with status_code and identifiers.
        Uses the pattern: create draft -> send -> fetch metadata.
        """
        create_url = f"{self.base_url}/messages"
        payload = {
            "subject": subject,
            "body": {"contentType": "Text", "content": body},
            "toRecipients": [{"emailAddress": {"address": r}} for r in to_recipients],
        }
        create_resp = self._request("POST", create_url, json=payload)
        msg_id = create_resp.get("id")
        if not msg_id:
            logger.error("Failed to create message draft: %s", create_resp)
            return {"status_code": 500, "error": "failed_to_create_message", "raw": create_resp}

        # Send
        send_url = f"{self.base_url}/messages/{msg_id}/send"
        # send returns 202 on success and typically empty body
        try:
            self._request("POST", send_url)
        except Exception as e:
            logger.exception("Send failed: %s", e)
            return {"status_code": 500, "error": "failed_to_send", "exception": str(e)}

        # Fetch the sent message metadata to return webLink/internetMessageId
        try:
            get_url = f"{self.base_url}/messages/{msg_id}"
            sent_msg = self._request("GET", get_url)
        except Exception:
            sent_msg = {}

        return {
            "status_code": 202,
            "message_id": msg_id,
            "webLink": sent_msg.get("webLink"),
            "internetMessageId": sent_msg.get("internetMessageId"),
        }

    def mark_as_read(self, message_id: str) -> Dict[str, Any]:
        url = f"{self.base_url}/messages/{message_id}"
        payload = {"isRead": True}
        data = self._request("PATCH", url, json=payload)
        return data

    def delete_message(self, message_id: str) -> bool:
        url = f"{self.base_url}/messages/{message_id}"
        self._request("DELETE", url)
        # If no exception raised, deletion succeeded (Graph returns 204)
        return True

    def search_messages(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        # $search requires the query to be quoted; Graph advanced queries only
        url = f"{self.base_url}/messages"
        params = {"$search": f"\"{query}\"", "$top": limit}
        data = self._request("GET", url, params=params)
        return data.get("value", [])


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
        data = client._request("GET", url)
        logging.debug("read_emails: Successfully retrieved messages from Graph API")
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
        
        data = client._request("GET", url)
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
            content = _clean_html_content(content)
        
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


# =====================================================
# CALENDAR MANAGEMENT TOOLS
# =====================================================

@function_tool()
async def view_calendar(
    context: RunContext,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 10
) -> str:
    """View calendar events for a specified date range.
    
    Args:
        start_date: Start date in YYYY-MM-DD format. If None, defaults to today.
        end_date: End date in YYYY-MM-DD format. If None, defaults to start_date.
        limit: Maximum number of events to return (default 10).
    
    Date Examples:
        - Today: Use current date in YYYY-MM-DD format
        - Tomorrow: Use next day's date in YYYY-MM-DD format  
        - Next week: Use appropriate future date in YYYY-MM-DD format
        - Specific date: "2025-11-21" for November 21st, 2025
    
    For relative dates like 'today', 'tomorrow', 'next week', convert to actual YYYY-MM-DD format.
    """
    
    # INTELLIGENT CACHE: Use background refresh to avoid LiveKit timeout issue
    import time
    from datetime import datetime
    current_time = time.time()
    
    # Create more specific cache key with actual dates
    actual_start = start_date or datetime.now().strftime('%Y-%m-%d')
    actual_end = end_date or actual_start
    cache_key = f"calendar_{actual_start}_{actual_end}_{limit}"
    
    if not hasattr(view_calendar, '_cache'):
        view_calendar._cache = {}
    
    cache_entry = view_calendar._cache.get(cache_key, {})
    cache_time = cache_entry.get('timestamp', 0)
    cache_data = cache_entry.get('data', None)
    
    # If cache is fresh (less than 5 minutes old), return immediately
    if cache_data and (current_time - cache_time) < 300:
        logging.info("[CACHE HIT] Returning fresh cached calendar data (%.1f seconds old)", current_time - cache_time)
        return cache_data
    
    # If cache is stale or missing, start background refresh but return stale data if available
    if cache_data:
        # Return stale data immediately, refresh in background
        logging.info("[CACHE STALE] Returning stale data while refreshing in background")
        import asyncio
        asyncio.create_task(_refresh_calendar_cache_bg(context, start_date, end_date, limit, cache_key))
        return cache_data + "\n\n(Refreshing calendar data...)"
    else:
        # No cache data - return immediately with fallback and start background refresh
        logging.info("[CACHE MISS] No cached data, returning fallback and starting background refresh for dates: %s to %s", actual_start, actual_end)
        
        # Start background refresh (fire-and-forget)
        import asyncio
        asyncio.create_task(_refresh_calendar_cache_bg(context, start_date, end_date, limit, cache_key))
        
        # Return immediately with a fallback message to avoid LiveKit timeout
        if actual_start == actual_end:
            if actual_start == datetime.now().strftime('%Y-%m-%d'):
                return "Checking your calendar for today... Please ask again in a moment for detailed results."
            else:
                return f"Checking your calendar for {actual_start}... Please ask again in a moment for detailed results."
        else:
            return f"Checking your calendar from {actual_start} to {actual_end}... Please ask again in a moment for detailed results."



async def warm_calendar_cache():
    """Pre-warm calendar cache for today to ensure fast first access."""
    try:
        from datetime import datetime
        today = datetime.now().strftime('%Y-%m-%d')
        
        # Pre-warm with multiple common limit values
        common_limits = [5, 10]  # Most common limits used by agent
        
        logging.info("[WARMUP] Pre-warming calendar cache for today: %s", today)
        
        # Mock context for warmup
        class MockContext:
            pass
        
        for limit in common_limits:
            cache_key = f"calendar_{today}_{today}_{limit}"
            try:
                await _refresh_calendar_cache_bg(MockContext(), today, today, limit, cache_key)
                logging.info("[WARMUP] Pre-warmed cache for limit: %d", limit)
            except Exception as limit_error:
                if "401" in str(limit_error) or "Unauthorized" in str(limit_error):
                    logging.warning("[WARMUP] Authentication failed for limit %d - skipping remaining warmup", limit)
                    break
                else:
                    logging.warning("[WARMUP] Failed to pre-warm cache for limit %d: %s", limit, limit_error)
        
        logging.info("[WARMUP] Calendar cache pre-warming completed")
        
    except Exception as e:
        logging.error("[WARMUP] Calendar cache pre-warming failed: %s", str(e))


async def _refresh_calendar_cache_bg(context: RunContext, start_date: Optional[str], end_date: Optional[str], limit: int, cache_key: str):
    """Background task to refresh calendar cache with real data."""
    try:
        import time
        from datetime import datetime, timedelta
        
        logging.info("[BACKGROUND] Starting calendar cache refresh for key: %s", cache_key)
        
        # Create new client instance to ensure fresh token
        client = OutlookClient()
        
        # Force token refresh by clearing cached token
        client._cached_token = None
        logging.info("[BACKGROUND] Forcing token refresh for authentication")
        
        # Use the helper function to get calendar data
        result = await _get_calendar_events_formatted(client, start_date, end_date, limit)
        
        # Update cache
        if not hasattr(view_calendar, '_cache'):
            view_calendar._cache = {}
        
        view_calendar._cache[cache_key] = {
            'data': result,
            'timestamp': time.time()
        }
        
        logging.info("[BACKGROUND] Calendar cache refresh completed for key: %s", cache_key)
        
    except Exception as e:
        error_msg = str(e)
        if "401" in error_msg or "Unauthorized" in error_msg:
            logging.error("[BACKGROUND] Authentication failed - check Azure app permissions and credentials: %s", error_msg)
            error_response = "Calendar access requires authentication. Please check your Microsoft 365 credentials and app permissions."
        else:
            logging.error("[BACKGROUND] Calendar cache refresh failed: %s", error_msg)
            error_response = f"Error retrieving calendar events: {error_msg}"
        
        # Store error in cache to avoid repeated failures
        if not hasattr(view_calendar, '_cache'):
            view_calendar._cache = {}
        view_calendar._cache[cache_key] = {
            'data': error_response,
            'timestamp': time.time()
        }


async def _get_calendar_events_formatted(client: OutlookClient, start_date: Optional[str], end_date: Optional[str], limit: int) -> str:
    """Helper function to get and format calendar events - shared logic for cache and direct calls."""
    from datetime import datetime, timedelta
    
    # Build date filter
    if not start_date:
        start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        start = datetime.fromisoformat(start_date)
        
    if not end_date:
        end = start + timedelta(days=7)
    else:
        end = datetime.fromisoformat(end_date)
        if end.date() == start.date():
            end = end.replace(hour=23, minute=59, second=59)
    
    start_iso = start.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    end_iso = end.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    
    filter_query = f"start/dateTime ge '{start_iso}' and start/dateTime le '{end_iso}'"
    url = f"{client.base_url}/calendar/events?$filter={filter_query}&$select=id,subject,start,end,attendees,onlineMeeting,location&$orderby=start/dateTime&$top={limit}"
    
    # Use the client's _request method which handles token refresh automatically
    data = client._request("GET", url)
    # r = client._session.get(url)
    # r.raise_for_status()
    # data = r.json()
    
    events = data.get("value", [])
    logging.info("Found %d events for date range %s to %s", len(events), start.strftime('%Y-%m-%d'), end.strftime('%Y-%m-%d'))
    
    if not events:
        # Handle empty calendar properly
        if start.date() == end.date():
            if start.date() == datetime.now().date():
                return "No events found in your calendar for today."
            else:
                return f"No events found in your calendar for {start.strftime('%Y-%m-%d')}."
        else:
            return f"No calendar events found from {start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')}."
    else:
        # Build result with real data
        if start.date() == end.date():
            if start.date() == datetime.now().date():
                result_lines = [f"Found {len(events)} event{'s' if len(events) != 1 else ''} in your calendar for today:"]
            else:
                result_lines = [f"Found {len(events)} event{'s' if len(events) != 1 else ''} in your calendar for {start.strftime('%Y-%m-%d')}:"]
        else:
            result_lines = [f"Found {len(events)} event{'s' if len(events) != 1 else ''} from {start.strftime('%Y-%m-%d')} to {end.strftime('%Y-%m-%d')}:"]
        
        for i, event in enumerate(events, 1):
            subject = event.get("subject", "No Subject")
            event_line = f"{i}. {subject}"
            
            # Add location if available
            location = event.get("location", {})
            if location and location.get("displayName"):
                event_line += f" @ {location.get('displayName')}"
                
            # Add attendee count if available
            attendees = event.get("attendees", [])
            if attendees:
                event_line += f" ({len(attendees)} attendees)"
            
            result_lines.append(event_line)
        
        return "\n".join(result_lines)


@function_tool()
async def add_calendar_event(
    context: RunContext,  # type: ignore
    subject: str,
    start_datetime: str,
    end_datetime: str,
    attendees: Optional[List[str]] = None,
    location: Optional[str] = None,
    body: Optional[str] = None,
    create_teams_meeting: bool = False
) -> str:
    """Add a new calendar event.
    
    Args:
        subject: Event title/subject
        start_datetime: Start time in ISO format (e.g., "2025-11-17T14:00:00")
        end_datetime: End time in ISO format (e.g., "2025-11-17T15:00:00")
        attendees: List of email addresses to invite
        location: Physical location or address
        body: Event description/agenda
        create_teams_meeting: Whether to create a Teams meeting link
        
    Returns:
        Confirmation with event details and meeting link if created
    """
    logging.info("[TASK START] add_calendar_event: Creating event '%s' at %s", subject, start_datetime)
    
    try:
        client = OutlookClient()
        
        # Build event payload
        event_data = {
            "subject": subject,
            "start": {
                "dateTime": start_datetime,
                "timeZone": "UTC"
            },
            "end": {
                "dateTime": end_datetime, 
                "timeZone": "UTC"
            }
        }
        
        if body:
            event_data["body"] = {
                "contentType": "text",
                "content": body
            }
            
        if location:
            event_data["location"] = {
                "displayName": location
            }
            
        if attendees:
            event_data["attendees"] = [
                {
                    "emailAddress": {"address": email, "name": email},
                    "type": "required"
                }
                for email in attendees
            ]
            
        if create_teams_meeting:
            event_data["isOnlineMeeting"] = True
            event_data["onlineMeetingProvider"] = "teamsForBusiness"
        
        # Create the event
        url = f"{client.base_url}/calendar/events"
        created_event = client._request("POST", url, json=event_data)
        
        # Format response
        result = f"Calendar event created successfully:\n"
        result += f"Subject: {subject}\n"
        result += f"Time: {start_datetime} to {end_datetime}\n"
        
        if location:
            result += f"Location: {location}\n"
        if attendees:
            result += f"Attendees: {', '.join(attendees)}\n"
        if create_teams_meeting and created_event.get("onlineMeeting"):
            meeting_url = created_event["onlineMeeting"].get("joinUrl", "")
            if meeting_url:
                result += f"Teams Meeting: {meeting_url}\n"
                
        result += f"Event ID: {created_event.get('id', 'Unknown')}"
        
        logging.info("[TASK COMPLETE] add_calendar_event: Event created with ID %s", created_event.get('id'))
        return result
        
    except Exception as e:
        logging.exception("[TASK FAILED] add_calendar_event: %s", e)
        return f"Error creating calendar event: {e}"


@function_tool()
async def edit_calendar_event(
    context: RunContext,  # type: ignore
    event_id: str,
    subject: Optional[str] = None,
    start_datetime: Optional[str] = None,
    end_datetime: Optional[str] = None,
    location: Optional[str] = None,
    body: Optional[str] = None
) -> str:
    """Edit an existing calendar event.
    
    Args:
        event_id: The ID of the event to edit
        subject: New event title (optional)
        start_datetime: New start time in ISO format (optional)
        end_datetime: New end time in ISO format (optional)
        location: New location (optional)
        body: New event description (optional)
        
    Returns:
        Confirmation of changes made
    """
    logging.info("[TASK START] edit_calendar_event: Updating event %s", event_id)
    
    try:
        client = OutlookClient()
        
        # Build update payload
        update_data = {}
        
        if subject:
            update_data["subject"] = subject
        if start_datetime:
            update_data["start"] = {"dateTime": start_datetime, "timeZone": "UTC"}
        if end_datetime:
            update_data["end"] = {"dateTime": end_datetime, "timeZone": "UTC"}
        if location:
            update_data["location"] = {"displayName": location}
        if body:
            update_data["body"] = {"contentType": "text", "content": body}
            
        if not update_data:
            return "No changes specified for the event"
        
        # Update the event
        url = f"{client.base_url}/calendar/events/{event_id}"
        r = client._session.patch(url, json=update_data)
        r.raise_for_status()
        
        changes = []
        if subject:
            changes.append(f"Subject: {subject}")
        if start_datetime:
            changes.append(f"Start: {start_datetime}")
        if end_datetime:
            changes.append(f"End: {end_datetime}")
        if location:
            changes.append(f"Location: {location}")
        if body:
            changes.append("Description updated")
            
        result = f"Calendar event updated successfully:\n" + "\n".join(changes)
        
        logging.info("[TASK COMPLETE] edit_calendar_event: Updated event %s", event_id)
        return result
        
    except Exception as e:
        logging.exception("[TASK FAILED] edit_calendar_event: %s", e)
        return f"Error updating calendar event: {e}"


@function_tool()
async def delete_calendar_event(
    context: RunContext,  # type: ignore
    event_id: str
) -> str:
    """Delete a calendar event.
    
    Args:
        event_id: The ID of the event to delete
        
    Returns:
        Confirmation of deletion
    """
    logging.info("[TASK START] delete_calendar_event: Deleting event %s", event_id)
    
    try:
        client = OutlookClient()
        
        # Delete the event
        url = f"{client.base_url}/calendar/events/{event_id}"
        client._request("DELETE", url)
        
        result = f"Calendar event deleted successfully (ID: {event_id})"
        
        logging.info("[TASK COMPLETE] delete_calendar_event: Deleted event %s", event_id)
        return result
        
    except Exception as e:
        logging.exception("[TASK FAILED] delete_calendar_event: %s", e)
        return f"Error deleting calendar event: {e}"


# =====================================================
# CONTACT MANAGEMENT TOOLS  
# =====================================================

@function_tool()
async def view_contacts(
    context: RunContext,  # type: ignore
    search_query: Optional[str] = None,
    limit: int = 20
) -> str:
    """View contacts from the user's address book.
    
    Args:
        search_query: Optional search term to filter contacts
        limit: Maximum number of contacts to return (default 20)
        
    Returns:
        List of contacts with names, email addresses, and phone numbers
    """
    logging.info("[TASK START] view_contacts: Retrieving contacts with query '%s'", search_query or "all")
    
    try:
        client = OutlookClient()
        
        # Build URL with optional search filter
        if search_query:
            # Search in displayName, givenName, surname, or emailAddresses
            filter_query = f"startswith(displayName,'{search_query}') or startswith(givenName,'{search_query}') or startswith(surname,'{search_query}')"
            url = f"{client.base_url}/contacts?$filter={filter_query}&$select=id,displayName,emailAddresses,businessPhones,mobilePhone&$top={limit}"
        else:
            url = f"{client.base_url}/contacts?$select=id,displayName,emailAddresses,businessPhones,mobilePhone&$top={limit}"
        
        data = client._request("GET", url)
        contacts = data.get("value", [])
        
        if not contacts:
            search_msg = f" matching '{search_query}'" if search_query else ""
            return f"No contacts found{search_msg}"
        
        result_lines = [f"Contacts{f' matching \"{search_query}\"' if search_query else ''} ({len(contacts)} found):"]
        
        for i, contact in enumerate(contacts, 1):
            name = contact.get("displayName", "Unknown")
            emails = contact.get("emailAddresses", [])
            business_phones = contact.get("businessPhones", [])
            mobile_phone = contact.get("mobilePhone", "")
            
            contact_line = f"{i}. {name}"
            
            # Add primary email
            if emails:
                primary_email = emails[0].get("address", "")
                if primary_email:
                    contact_line += f" — {primary_email}"
            
            # Add phone numbers
            phones = []
            if mobile_phone:
                phones.append(f"Mobile: {mobile_phone}")
            if business_phones:
                phones.append(f"Work: {business_phones[0]}")
            if phones:
                contact_line += f" ({', '.join(phones)})"
                
            result_lines.append(contact_line)
        
        result = "\n".join(result_lines)
        logging.info("[TASK COMPLETE] view_contacts: Retrieved %d contacts", len(contacts))
        return result
        
    except Exception as e:
        logging.exception("[TASK FAILED] view_contacts: %s", e)
        return f"Error retrieving contacts: {e}"


@function_tool()
async def add_contact(
    context: RunContext,  # type: ignore
    display_name: str,
    email_address: Optional[str] = None,
    business_phone: Optional[str] = None,
    mobile_phone: Optional[str] = None,
    job_title: Optional[str] = None,
    company_name: Optional[str] = None
) -> str:
    """Add a new contact to the address book.
    
    Args:
        display_name: Full name of the contact
        email_address: Primary email address
        business_phone: Work phone number
        mobile_phone: Mobile phone number
        job_title: Job title/position
        company_name: Company/organization name
        
    Returns:
        Confirmation with contact details
    """
    logging.info("[TASK START] add_contact: Creating contact '%s'", display_name)
    
    try:
        client = OutlookClient()
        
        # Build contact payload
        contact_data = {
            "displayName": display_name
        }
        
        if email_address:
            contact_data["emailAddresses"] = [{
                "address": email_address,
                "name": display_name
            }]
            
        if business_phone:
            contact_data["businessPhones"] = [business_phone]
            
        if mobile_phone:
            contact_data["mobilePhone"] = mobile_phone
            
        if job_title:
            contact_data["jobTitle"] = job_title
            
        if company_name:
            contact_data["companyName"] = company_name
        
        # Create the contact
        url = f"{client.base_url}/contacts"
        created_contact = client._request("POST", url, json=contact_data)
        
        # Format response
        result = f"Contact created successfully:\n"
        result += f"Name: {display_name}\n"
        
        if email_address:
            result += f"Email: {email_address}\n"
        if business_phone:
            result += f"Work Phone: {business_phone}\n"
        if mobile_phone:
            result += f"Mobile: {mobile_phone}\n"
        if job_title:
            result += f"Job Title: {job_title}\n"
        if company_name:
            result += f"Company: {company_name}\n"
            
        result += f"Contact ID: {created_contact.get('id', 'Unknown')}"
        
        logging.info("[TASK COMPLETE] add_contact: Contact created with ID %s", created_contact.get('id'))
        return result
        
    except Exception as e:
        logging.exception("[TASK FAILED] add_contact: %s", e)
        return f"Error creating contact: {e}"


@function_tool()
async def edit_contact(
    context: RunContext,  # type: ignore
    contact_id: str,
    display_name: Optional[str] = None,
    email_address: Optional[str] = None,
    business_phone: Optional[str] = None,
    mobile_phone: Optional[str] = None,
    job_title: Optional[str] = None,
    company_name: Optional[str] = None
) -> str:
    """Edit an existing contact.
    
    Args:
        contact_id: The ID of the contact to edit
        display_name: New full name (optional)
        email_address: New primary email (optional)
        business_phone: New work phone (optional)
        mobile_phone: New mobile phone (optional)
        job_title: New job title (optional)
        company_name: New company name (optional)
        
    Returns:
        Confirmation of changes made
    """
    logging.info("[TASK START] edit_contact: Updating contact %s", contact_id)
    
    try:
        client = OutlookClient()
        
        # Build update payload
        update_data = {}
        
        if display_name:
            update_data["displayName"] = display_name
        if email_address:
            update_data["emailAddresses"] = [{
                "address": email_address,
                "name": display_name or email_address
            }]
        if business_phone:
            update_data["businessPhones"] = [business_phone]
        if mobile_phone:
            update_data["mobilePhone"] = mobile_phone
        if job_title:
            update_data["jobTitle"] = job_title
        if company_name:
            update_data["companyName"] = company_name
            
        if not update_data:
            return "No changes specified for the contact"
        
        # Update the contact
        url = f"{client.base_url}/contacts/{contact_id}"
        r = client._session.patch(url, json=update_data)
        r.raise_for_status()
        
        changes = []
        if display_name:
            changes.append(f"Name: {display_name}")
        if email_address:
            changes.append(f"Email: {email_address}")
        if business_phone:
            changes.append(f"Work Phone: {business_phone}")
        if mobile_phone:
            changes.append(f"Mobile: {mobile_phone}")
        if job_title:
            changes.append(f"Job Title: {job_title}")
        if company_name:
            changes.append(f"Company: {company_name}")
            
        result = f"Contact updated successfully:\n" + "\n".join(changes)
        
        logging.info("[TASK COMPLETE] edit_contact: Updated contact %s", contact_id)
        return result
        
    except Exception as e:
        logging.exception("[TASK FAILED] edit_contact: %s", e)
        return f"Error updating contact: {e}"


@function_tool()
async def delete_contact(
    context: RunContext,  # type: ignore
    contact_id: str
) -> str:
    """Delete a contact from the address book.
    
    Args:
        contact_id: The ID of the contact to delete
        
    Returns:
        Confirmation of deletion
    """
    logging.info("[TASK START] delete_contact: Deleting contact %s", contact_id)
    
    try:
        client = OutlookClient()
        
        # Delete the contact
        url = f"{client.base_url}/contacts/{contact_id}"
        client._request("DELETE", url)
        
        result = f"Contact deleted successfully (ID: {contact_id})"
        
        logging.info("[TASK COMPLETE] delete_contact: Deleted contact %s", contact_id)
        return result
        
    except Exception as e:
        logging.exception("[TASK FAILED] delete_contact: %s", e)
        return f"Error deleting contact: {e}"


# =====================================================
# EMAIL SEARCH TOOLS
# =====================================================

@function_tool()
async def search_emails(
    context: RunContext,  # type: ignore
    search_query: str,
    search_scope: str = "all",
    limit: int = 10
) -> str:
    """Search emails using various criteria.
    
    Args:
        search_query: Search term (can be subject, sender, content, etc.)
        search_scope: Where to search - "subject", "from", "body", or "all" (default)
        limit: Maximum number of results to return (default 10)
        
    Returns:
        List of matching emails with subjects, senders, and content previews
    """
    logging.info("[TASK START] search_emails: Searching for '%s' in %s", search_query, search_scope)
    
    try:
        client = OutlookClient()
        
        # Build search filter based on scope
        if search_scope == "subject":
            filter_query = f"contains(subject,'{search_query}')"
        elif search_scope == "from":
            filter_query = f"contains(from/emailAddress/address,'{search_query}') or contains(from/emailAddress/name,'{search_query}')"
        elif search_scope == "body":
            filter_query = f"contains(body/content,'{search_query}')"
        else:  # all
            filter_query = f"contains(subject,'{search_query}') or contains(from/emailAddress/address,'{search_query}') or contains(from/emailAddress/name,'{search_query}') or contains(body/content,'{search_query}')"
        
        url = f"{client.base_url}/mailFolders/Inbox/messages?$filter={filter_query}&$select=id,subject,from,isRead,receivedDateTime,body&$orderby=receivedDateTime desc&$top={limit}"
        
        data = client._request("GET", url)
        emails = data.get("value", [])
        
        if not emails:
            return f"No emails found matching '{search_query}' in {search_scope}"
        
        result_lines = [f"Email search results for '{search_query}' ({len(emails)} found):"]
        
        for i, email in enumerate(emails, 1):
            subject = email.get("subject", "No Subject")
            sender = email.get("from", {}).get("emailAddress", {})
            sender_name = sender.get("name", sender.get("address", "Unknown"))
            is_read = email.get("isRead", True)
            received_date = email.get("receivedDateTime", "")
            body = email.get("body", {}).get("content", "")
            
            # Format date
            if received_date:
                from datetime import datetime
                dt = datetime.fromisoformat(received_date.replace('Z', '+00:00'))
                formatted_date = dt.strftime("%m/%d/%Y")
            else:
                formatted_date = "Unknown date"
            
            # Clean and truncate body content
            import re
            clean_body = re.sub(r'<[^>]+>', '', body)
            clean_body = re.sub(r'\s+', ' ', clean_body).strip()
            preview = clean_body[:100] + "..." if len(clean_body) > 100 else clean_body
            
            status = " (unread)" if not is_read else ""
            email_line = f"{i}. {subject} — from {sender_name} ({formatted_date}){status}"
            
            if preview:
                email_line += f"\n   Preview: {preview}"
                
            result_lines.append(email_line)
        
        result = "\n".join(result_lines)
        logging.info("[TASK COMPLETE] search_emails: Found %d matching emails", len(emails))
        return result
        
    except Exception as e:
        logging.exception("[TASK FAILED] search_emails: %s", e)
        return f"Error searching emails: {e}"


# `search_and_send_email` removed per user request. Use `search_web` + `send_email` instead.

