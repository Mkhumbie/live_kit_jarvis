"""
Direct test to see if we can invoke read_emails and capture the result
"""
import asyncio
from tools import read_emails

async def test_with_debugging():
    print("=== DIRECT READ_EMAILS TEST ===")
    
    # Test 1: Call read_emails directly
    print("1. Calling read_emails directly...")
    result = await read_emails(context=None, limit=3)
    print(f"Direct result: {result}")
    print(f"Result type: {type(result)}")
    print(f"Result contains TOOL_OUTPUT_START: {'TOOL_OUTPUT_START' in result}")
    
    # Test 2: Check if debug file was created
    print("\n2. Checking debug file...")
    try:
        with open("debug_tool_result.txt", "r", encoding="utf-8") as f:
            content = f.read()
            print(f"Debug file content:\n{content}")
    except FileNotFoundError:
        print("Debug file not found")
    except Exception as e:
        print(f"Error reading debug file: {e}")
    
    # Test 3: Verify the tool result format
    print("\n3. Result analysis...")
    if result:
        lines = result.split('\n')
        print(f"Result has {len(lines)} lines")
        for i, line in enumerate(lines[:3], 1):
            print(f"Line {i}: {line}")
    
    return result

if __name__ == "__main__":
    asyncio.run(test_with_debugging())