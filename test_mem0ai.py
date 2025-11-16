# test_mem0ai_resilient.py
from dotenv import load_dotenv
from mem0 import MemoryClient
import logging
import json
import time

load_dotenv()

user_name = "David"
mem0 = MemoryClient()

def add_memory_resilient():
    """
    Try multiple add() signatures to work with different mem0 client versions.
    If the API queues the job in background, we'll poll search a few times.
    """

    plain_text_memory = "David likes Linkin Park."

    print("➡️  Attempting to add memory...")

    # Try variant 1: memory= (some versions accept this and save instantly)
    try:
        response = mem0.add(memory=plain_text_memory, user_id=user_name)
        print("add() called with memory= -> response:", response)
    except TypeError as e:
        # If the signature doesn't accept memory=, fall back to messages=
        print("memory= not supported by this mem0 client (TypeError). Falling back to messages= ...")
        try:
            messages = [
                {"role": "user", "content": plain_text_memory}
            ]
            response = mem0.add(messages=messages, user_id=user_name)
            print("add() called with messages= -> response:", response)
        except Exception as e2:
            print("Failed to add with messages= as well. Exception:", repr(e2))
            raise

    except Exception as e:
        # Any other unexpected exception
        print("Unexpected error while calling add():", repr(e))
        raise

    return response

def search_until_found(timeout_seconds=8, poll_interval=1):
    """
    Search repeatedly for the newly added memory until it's found or timeout.
    Returns the list of found memories (may be empty).
    """
    deadline = time.time() + timeout_seconds
    attempt = 1
    while time.time() < deadline:
        print(f"\n🔎 Search attempt #{attempt} ...")
        results = mem0.search(
            query=f"What are {user_name}'s preferences?",
            filters={"user_id": user_name}
        )
        print("RAW SEARCH RESULTS:", results)

        items = results.get("results", []) if isinstance(results, dict) else []
        if items:
            print("Memory found!")
            formatted = [{"memory": i.get("memory"), "updated_at": i.get("updated_at")} for i in items]
            print(json.dumps(formatted, indent=2))
            return formatted

        # nothing yet — wait and retry
        attempt += 1
        time.sleep(poll_interval)

    print("Timed out waiting for the memory to appear in search.")
    return []

def list_all_memories_safe():
    """
    Calls get_all with required filters to avoid 400 errors.
    """
    print("\n➡️  Listing all memories for user:", user_name)
    results = mem0.get_all(filters={"user_id": user_name})
    print("RAW ALL MEMORIES:", results)

    items = results.get("results", []) if isinstance(results, dict) else []
    formatted = [{"memory": i.get("memory"), "updated_at": i.get("updated_at")} for i in items]
    print(json.dumps(formatted, indent=2))
    return formatted

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # 1) Try to add memory (resiliently)
    add_resp = add_memory_resilient()

    # 2) Immediately search; if job is queued we poll for a short time
    found = search_until_found(timeout_seconds=10, poll_interval=1)

    # 3) List all memories (use filters param)
    try:
        all_mem = list_all_memories_safe()
    except Exception as e:
        print("Error calling get_all():", repr(e))
