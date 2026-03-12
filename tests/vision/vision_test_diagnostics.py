import os
import sys
import asyncio
import base64
from io import BytesIO
import pyautogui
from PIL import Image
import cv2
from google import genai
import requests
from google.genai import types
from dotenv import load_dotenv

# Add project root to path to allow relative imports if needed
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

load_dotenv()

async def test_screen_capture():
    print("\n--- [1] Screen Capture Test ---")
    try:
        screenshot = pyautogui.screenshot()
        print(f"[OK] Success! Captured screen size: {screenshot.size}")
        
        # Save a sample to verify
        save_path = os.path.abspath("test_screenshot.png")
        screenshot.save(save_path)
        print(f"INFO: Sample saved to: {save_path}")
        return True
    except Exception as e:
        print(f"[ERROR] Error: Screen capture failed: {str(e).encode('ascii', 'ignore').decode()}")
        return False

async def test_camera_capture():
    print("\n--- [2] Local Webcam Test (OpenCV) ---")
    try:
        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            print("[ERROR] Error: Could not open webcam.")
            return False
        
        ret, frame = cap.read()
        if ret:
            print("[OK] Success! Frame captured from webcam.")
            save_path = os.path.abspath("test_camera.jpg")
            cv2.imwrite(save_path, frame)
            print(f"INFO: Sample saved to: {save_path}")
            cap.release()
            return True
        else:
            print("[ERROR] Error: Could not read frame from webcam.")
            cap.release()
            return False
    except Exception as e:
        print(f"[ERROR] Error: Webcam test failed: {str(e).encode('ascii', 'ignore').decode()}")
        return False

async def test_gemini_api():
    print("\n--- [3] Gemini API Connectivity Test ---")
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        print("[ERROR] Error: GOOGLE_API_KEY not found in .env")
        return False
    
    try:
        client = genai.Client(api_key=api_key)
        
        # List models to see what's available
        print("Checking available models...")
        models_list = []
        for m in client.models.list():
            models_list.append(m.name)
        
        # Filter for models that support generateContent
        print(f"INFO: Total models found: {len(models_list)}")
        print(f"INFO: Sample names: {models_list[:5]}")
        
        # Try to find a standard vision model
        target_model = "gemini-1.5-flash"
        # Check if the model name needs a prefix or is different
        model_names_only = [m.split('/')[-1] for m in models_list]
        
        if target_model not in model_names_only:
             print(f"WARNING: {target_model} not in simple list.")
             if "gemini-2.0-flash" in model_names_only:
                 target_model = "gemini-2.0-flash"
             elif len(model_names_only) > 0:
                 # Just pick the first likely one if 1.5 flash isn't there
                 for name in model_names_only:
                     if "gemini" in name and "flash" in name:
                         target_model = name
                         break
        
        print(f"INFO: Using target_model: {target_model}")

        # Create a tiny dummy image
        img = Image.new('RGB', (100, 100), color = 'red')
        
        print(f"Sending test request to Gemini using {target_model}...")
        response = await asyncio.to_thread(
            client.models.generate_content,
            model=target_model,
            contents=["What color is this image?", img]
        )
        
        if response.text:
            cleaned_text = response.text.strip().encode('ascii', 'ignore').decode()
            print(f"[OK] Success! Gemini response: {cleaned_text}")
            return True
        else:
            print("[ERROR] Error: Gemini returned an empty response.")
            return False
    except Exception as e:
        error_msg = str(e).encode('ascii', 'ignore').decode()
        print(f"[ERROR] Error: Gemini API test failed: {error_msg}")
        return False

async def test_openrouter_api():
    print("\n--- [4] OpenRouter Connectivity Test ---")
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        print("WARNING: OPENROUTER_API_KEY not found. Skipping fallback test.")
        return True
    
    try:
        # Create a tiny dummy image
        img = Image.new('RGB', (100, 100), color = 'blue')
        buffered = BytesIO()
        img.save(buffered, format="JPEG")
        img_b64 = base64.b64encode(buffered.getvalue()).decode('utf-8')

        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": "nvidia/nemotron-nano-12b-v2-vl:free",
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "What color is this image?"},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}}
                ]
            }]
        }

        print("Sending test request to OpenRouter...")
        response = await asyncio.to_thread(requests.post, url, headers=headers, json=payload, timeout=30)
        
        if response.status_code == 200:
            result = response.json()
            content = result['choices'][0]['message']['content']
            cleaned_content = content.strip().encode('ascii', 'ignore').decode()
            print(f"[OK] Success! OpenRouter response: {cleaned_content}")
            return True
        else:
            print(f"[ERROR] Error: OpenRouter returned status {response.status_code}: {response.text}")
            return False
    except Exception as e:
        error_msg = str(e).encode('ascii', 'ignore').decode()
        print(f"[ERROR] Error: OpenRouter API test failed: {error_msg}")
        return False

async def test_groq_api():
    print("\n--- [5] Groq API Connectivity Test ---")
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        print("WARNING: GROQ_API_KEY not found. Skipping Groq test.")
        return False

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")
        print("Sending test request to Groq (Llama 4 Scout Vision)...")
        
        response = await asyncio.to_thread(
            client.chat.completions.create,
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[{"role": "user", "content": "Say 'Groq Ready'"}],
            max_tokens=10
        )
        msg = response.choices[0].message.content
        print(f"[OK] Success! Groq response: {msg.strip()}")
        return True
    except Exception as e:
        error_msg = str(e).encode('ascii', 'ignore').decode()
        print(f"[ERROR] Error: Groq API test failed: {error_msg}")
        return False

async def run_diagnostics():
    print("========================================")
    print("   JARVIS VISION DIAGNOSTICS SCRIPT    ")
    print("========================================\n")
    
    results = {
        "screen": await test_screen_capture(),
        "camera": await test_camera_capture(),
        "gemini": await test_gemini_api(),
        "groq": await test_groq_api(),
        "openrouter": await test_openrouter_api()
    }
    
    print("\n========================================")
    print("           SUMMARY REPORT              ")
    print("========================================")
    for key, val in results.items():
        status = "PASSED [OK]" if val else "FAILED [ERROR]"
        print(f"{key.capitalize():<15}: {status}")
    print("========================================\n")

if __name__ == "__main__":
    asyncio.run(run_diagnostics())
