#!/bin/bash
echo "🚀 [JARVIS PROD INSTALL] Starting Linux/Mac installation..."

# 1. Check for Python
if ! command -v python3 &> /dev/null
then
    echo "❌ [ERROR] Python3 not found. Please install it."
    exit 1
fi

# 2. Setup Virtual Environment
echo "📦 [STEP 1] Setting up Virtual Environment..."
python3 -m venv venv
source venv/bin/activate

# 3. Install Dependencies
echo "🛠️ [STEP 2] Installing Core Dependencies..."
pip install --upgrade pip
if [ -f requirements_audit.txt ]; then
    pip install -r requirements_audit.txt
fi

# 4. Create local .env
if [ ! -f .env ]; then
    echo "🔑 [STEP 3] Creating .env from template..."
    cp .env.production.example .env
    echo "⚠️ [IMPORTANT] Please edit .env with your real API keys!"
fi

# 5. Create Log Directories
mkdir -p logs backups Jarvis_Outputs

echo "✅ [COMPLETED] JARVIS-AGENT is ready for production."
echo "To start: python3 services/utils/watchdog.py"
