import asyncio
import sys
import os

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from services.ai_core.jarvis_vision import analyze_screen

def safe_print(text):
    """Helper to print text safely on Windows console with non-ASCII characters."""
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode('ascii', 'replace').decode('ascii'))

async def run_screen_test():
    safe_print("========================================")
    safe_print("   JARVIS STANDALONE SCREEN VISION TEST")
    safe_print("========================================\n")

    safe_print("[STEP 1] Capturing and analyzing primary monitor...")
    
    # We ask a specific, detailed prompt to test reasoning
    prompt = "Detailed analysis of everything visible on the screen. Mention open applications, code snippets, or UI elements you see."
    
    try:
        result = await analyze_screen(prompt)
        
        if result.get("status") == "success":
            safe_print("\n[SUCCESS] AI Screen Description:")
            safe_print("-" * 40)
            safe_print(result.get("message", "No description provided."))
            safe_print("-" * 40)
            
            # Check if screenshot was saved (internal logic of analyze_screen saves to Jarvis_Outputs)
            output_dir = "Jarvis_Outputs"
            if os.path.exists(output_dir):
                files = [f for f in os.listdir(output_dir) if f.startswith("screen_")]
                if files:
                    latest_file = max([os.path.join(output_dir, f) for f in files], key=os.path.getctime)
                    safe_print(f"\n[INFO] Screenshot saved to: {latest_file}")
        else:
            safe_print(f"\n[ERROR] Screen analysis failed: {result.get('message')}")
            
    except Exception as e:
        safe_print(f"\n[EXCEPTION] An error occurred: {e}")

    safe_print("\n========================================")
    safe_print("          TEST COMPLETE                 ")
    safe_print("========================================")

if __name__ == "__main__":
    asyncio.run(run_screen_test())
