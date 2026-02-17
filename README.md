# 🤖 JARVIS – Advanced Real-Time AI Personal Assistant

> **Engineered for Excellence by Matloob (PU STUDENTS)**

JARVIS is a professional-grade, **autonomous AI Personal Assistant** designed to bridge the gap between human language and Windows system control. Unlike standard automation scripts, JARVIS utilizes advanced reasoning, vision intelligence, and proactive monitoring to provide a seamless, sentient-like experience.

---

## 🌟 Premium Features

### 👁️ Vision Intelligence

- **Real-Time Screen Perception**: JARVIS can "see" and analyze your primary monitor.
- **UI Interaction**: Identifies open applications, text on screen, and UI elements to assist you contextually.

### 📄 Intelligent Document Analysis (RAG)

- **Local Knowledge Base**: Search, read, and summarize PDF and Word documents from your local drives.
- **Smart Querying**: Ask natural language questions about your invoices, reports, or resumes.

### 🧠 Autonomous Multi-Step Planning

- **Complex Task Decomposition**: Decompounds broad requests (e.g., "Find images, zip them, and email to X") into sequential, logical execution steps.
- **Fail-Safe Execution**: Robust error handling at every step of the autonomous chain.

### 📧 Real-Time Communication Suite

- **Verified SMTP Integration**: Securely sends real emails via Gmail with attachment support.
- **WhatsApp Cloud Automation**: Hands-free messaging with natural Urdu (Roman script) support.

### 🖼️ High-Res Media Automation

- **Real-Time Image Engine**: Fetches and downloads actual high-resolution images from the web (not placeholders).
- **Intelligent Archiving**: Automatic Zipping and organization of downloaded assets.

### 📋 Proactive Clipboard Monitor (NEW!)

- **Error Detection**: Background monitoring of the clipboard for technical tracebacks and error codes.
- **Instant Solutions**: Automatically searches for solutions to detected errors and proactively suggests them to the user.

---

## 🛡️ Engineering Gold Standard

- **Pylint Score**: **10.00/10** (Zero warnings, zero errors).
- **Architecture**: Modular tool-based design for easy extensibility.
- **Security**: Environment-based secret management (using `.env`).
- **Language**: Dynamic support for Natural Urdu (Roman script) and technical English.

---

## 🛠️ Usage & Setup

1. **Clone & Enter**:

   ```bash
   git clone https://github.com/Matloob11/JARVIS-AGENT.git
   cd JARVIS-AGENT
   ```

2. **Environment Setup**:

   ```bash
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```

3. **Configuration**:
   Add your keys to `.env`:
   - `GOOGLE_API_KEY`: For Gemini Vision/Audio
   - `EMAIL_USER` & `EMAIL_APP_PASSWORD`: For real email sending

4. **Launch JARVIS**:
   ```bash
   python agent.py
   ```

---

## 📂 System Architecture

- **`agent.py`**: The central brain handling job sessions and tool routing.
- **`jarvis_vision.py`**: Multinodal vision processing engine.
- **`jarvis_rag.py`**: PDF/Word parsing and fuzzy search logic.
- **`jarvis_clipboard.py`**: Proactive background error monitor.
- **`jarvis_advanced_tools.py`**: High-level utilities (Email, Zip, DDGS Search).
- **`jarvis_reasoning.py`**: Intent analysis and LLM thought patterns.

---

> **JARVIS is not just an assistant; it's a teammate.**
> **MADE BY MATLOOB (PU STUDENTS)**
