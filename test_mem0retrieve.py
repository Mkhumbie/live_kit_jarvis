import os
from mem0 import MemoryClient

# -----------------------------------------
#  INITIALIZE CLIENT
# -----------------------------------------
mem0 = MemoryClient()

USER_ID = "David"   # any string you want

# -----------------------------------------
#  RETRIEVE MEMORY FOR USER USING FILTERS
# -----------------------------------------
def retrieve_memories():
    print("\n➡️  Retrieving memories for user:", USER_ID)

    try:
        response = mem0.get_all(
            filters={"user_id": USER_ID}
        )
        print("\nRAW RESPONSE:", response)

        # Extract list
        memory_list = response.get("results", [])

        print("\nFormatted memories:")
        for m in memory_list:
            print(f"- {m.get('memory')}  (updated: {m.get('updated_at')})")

    except Exception as e:
        print("\n❌ ERROR retrieving memories:", e)


# -----------------------------------------
if __name__ == "__main__":
    retrieve_memories()
