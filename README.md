# 🔱 J.A.R.V.I.S Agent: Advanced Multimodal AI Assistant

> **The Elite Personal Assistant Ecosystem | Engineered by Sir Matloob**

---

<div align="center">
  <img src="access/jarvis_ui.png" width="800" alt="JARVIS Interface">
  <br>
  <img src="https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/Gemini-2.5%20Flash-4285F4?style=for-the-badge&logo=google&logoColor=white" alt="Gemini">
  <img src="https://img.shields.io/badge/LiveKit-Multimodal-FF0000?style=for-the-badge&logo=livekit&logoColor=white" alt="LiveKit">
  <img src="https://img.shields.io/badge/Status-Operational-00FF00?style=for-the-badge" alt="Status">
</div>

---

## 🌌 Project Vision

J.A.R.V.I.S (Just A Rather Very Intelligent System) is not just a chatbot; it's a **Multimodal Live Assistant** designed for professional automation, deep reasoning, and personalized emotional intelligence. Built on the **Google Gemini 2.5 Flash Native Audio** model and **LiveKit** infrastructure, it offers real-time voice, vision, and tool-augmented intelligence.

---

## 🎨 Dual-Persona Interface Analysis

| **JARVIS Mode**                                    | **ANNA Mode**                                         |
| :------------------------------------------------- | :---------------------------------------------------- |
| ![Jarvis HUD](ui_gifs/jarvis-ui.gif)               | ![Anna HUD](ui_gifs/anna_ui.gif)                      |
| **Persona**: Professional, Elite, Loyal Assistant. | **Persona**: Affectionate, Romantic, Protective "GF". |
| **Voice**: Charon (Deep, Professional Male).       | **Voice**: Aoede (Soft, Warm Female).                 |
| **Language**: Roman Urdu / English Mirroring.      | **Language**: Caring Roman Urdu (Natural).            |
| **UI Aesthetics**: Blue Tech HUD / 3D Analysis.    | **UI Aesthetics**: Cosmic Heart / Circuit Soul.       |

---

## 🧠 Brain Structure & Intelligence

### 1. 🧬 Multimodal Core

- **Gemini 2.5 Flash**: Native audio processing for near-zero latency, recognizing emotional nuances in voice.
- **Autonomous Reasoning**: Uses a dedicated logic engine to plan multi-step workflows before execution.
- **Self-Healing Protocol**: Automatically detects tool failures and attempts to repair its own codebase.

### 2. 🗃️ Advanced Memory System

- **Vector Memory (ChromaDB)**: Long-term archival of conversations with semantic search retrieval.
- **Identity Manager**: Persistently remembers facts about "Sir Matloob" and his background.
- **Context Extraction**: Proactively saves important snippets (reminders, preferences) from live conversation.

---

## 🛠️ Elite Tool Arsenal

| Feature              | Description                                                                   |
| :------------------- | :---------------------------------------------------------------------------- |
| 🌍 **Global Search** | Real-time internet research via Google Search and Perplexity-style synthesis. |
| 💻 **Code Forge**    | Generates, runs, and debugs Python/CMD scripts on the fly.                    |
| 📱 **Automation**    | Full control over WhatsApp, Notepad, and System Windows.                      |
| ☁️ **Weather AI**    | Real-time weather analytics with location awareness.                          |
| 📁 **File Engine**   | Folder management, file creation, zipping, and image downloading.             |
| 📧 **Messenger**     | Sends real-time emails via SMTP with attachments.                             |
| 🎥 **Media Suite**   | YouTube downloading, Ad-Free Playback trick, and Media playback.     |
| 🧹 **Diagnostics**   | Pre-flight system health checks to ensure 100% operational status.            |
| 🔒 **Voice Guard**   | Advanced biometric voice fingerprinting for owner-only access.                |

---

## 🚀 Professional Setup (A to Z)

### 📋 Prerequisites

- **Python 3.10 or 3.11** (recommended).
- **LiveKit Cloud** account (for real-time audio/RTC).
- **Google AI Studio** API Key (Gemini Flash).
- **System Dependencies**: OS-level audio drivers (PyAudio).

### ⚙️ Installation

1. **Clone the Forge**:

   ```bash
   git clone https://github.com/Matloob11/JARVIS-AGENT.git
   cd JARVIS-AGENT
   ```

2. **Establish Environment**:

   ```bash
   python -m venv .venv
   source .venv/bin/activate  # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```

3. **Configure the Core**:
   Create a `.env` file from `.env.example`:
   ```env
   LIVEKIT_URL=wss://your-project.livekit.cloud
   LIVEKIT_API_KEY=your_key
   LIVEKIT_API_SECRET=your_secret
   GOOGLE_API_KEY=your_gemini_key
   WEATHER_API_KEY=your_openweather_key
   ```

4. **🔐 Encrypt Sensitive Data (Recommended)**:
   ```bash
   python scripts/encrypt_env.py --backup --remove-original
   ```

### 🔱 Activation

Run the worker and the agent simultaneously:

```bash
python agent.py dev
```

_Wait for the "SYSTEM ONLINE" banner and the HUD to initialize._

### 🧪 Testing

Run comprehensive test suite:

```bash
# Run all tests
python scripts/run_tests.py

# Run voice security verification
python scripts/voice/realtime_voice_test.py

# Run YouTube ad-free verification
python scripts/verify_yt_fix.py

# Run specific test profiles
python scripts/run_tests.py --profile unit
```

### 📊 Monitoring

Start performance monitoring:

```bash
python scripts/start_monitoring.py
```

Access the monitoring dashboard at: `http://127.0.0.1:8000`

### 🛡️ Security Features

- **🔐 Biometric Voice Fingerprint**: Strict owner-only access with biometric matching.
- **🔐 API Key Encryption**: All sensitive data encrypted with AES-256.
- **🔐 Secure Configuration**: Encrypted environment file management.
- **🔐 Memory Protection**: Secure in-memory data handling.
- **🔐 Audit Logging**: Complete security audit trail.

### 📈 Performance Features

- **📊 Real-time Monitoring**: System, application, and LLM metrics
- **📊 Performance Dashboard**: Interactive web dashboard
- **📊 Alert System**: Automated performance alerts
- **📊 Historical Analysis**: Performance trend analysis

---

## 🖼️ Visual Analytics (From `access/` Collection)

### [Technical Workspace](access/coding.png)

The heart of the development, showing the `agent_core.py` logic where tools and personas converge. Built with highly modular, object-oriented principles.

### [Visual HUD Architecture](access/jarvis_ui.png)

A breakdown of the Jarvis HUD, featuring real-time CPU/RAM metrics, system logging streams, and audio-reactive animations.

---

## 🛡️ Security & Integrity

- **Restricted Access**: Enforces wake-word protocols ("Jarvis" / "Anna").
- **Encrypted Local Storage**: Identity and memories are stored in structured JSON formats with atomic write protection.
- **Diagnostic Guard**: Automated monitoring for memory leaks or socket hangs.

---

<p align="center">
  <b>Designed with ❤️ for Sir Matloob</b><br>
  <i>"Excellence is not an act, but a habit."</i>
</p>
