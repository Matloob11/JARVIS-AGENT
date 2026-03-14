import requests
import json
import time
import hmac
import hashlib

# Security Configuration
SECURITY_TOKEN = "vortex_secure_8899_prime"

def send_location_update(city, lat, lng):
    url = "http://127.0.0.1:5001/notify"
    
    # ui_bridge.py expects 'type' and 'payload'
    data = {
        "type": "location_sync",
        "payload": {
            "city": city,
            "lat": lat,
            "lng": lng
        }
    }
    
    # ui_bridge.py uses json.dumps(data, sort_keys=True) with default spaces
    payload_str = json.dumps(data, sort_keys=True)
    signature = hmac.new(
        SECURITY_TOKEN.encode(),
        payload_str.encode(),
        hashlib.sha256
    ).hexdigest()

    headers = {
        "X-Vortex-Token": SECURITY_TOKEN,
        "X-Vortex-Signature": signature,
        "Content-Type": "application/json"
    }
    
    print(f"Sending location update to: {city} ({lat}, {lng})")
    try:
        response = requests.post(url, data=payload_str, headers=headers, timeout=5)
        if response.status_code == 200:
            print("Successfully synced location!")
        else:
            print(f"Failed to sync location. Status: {response.status_code}, Error: {response.text}")
    except Exception as e:
        print(f"Error connecting to UI Bridge: {e}")

if __name__ == "__main__":
    # Test 1: Islamabad
    send_location_update("Islamabad", 33.7215, 73.0433)
    time.sleep(2)
    
    # Test 2: London
    send_location_update("London", 51.5074, -0.1278)
    time.sleep(2)
    
    # Test 3: New York
    send_location_update("New York", 40.7128, -74.0060)
    
    print("\nTest completed. Check dashboard to see map movement.")
