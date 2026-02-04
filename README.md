# 🤖 JARVIS – Advanced Real-Time AI Personal Assistant
> **MADE BY MATLOOB**

JARVIS is a next-generation **real-time AI personal assistant** designed to control your Windows environment, automate tasks, and provide intelligent assistance. Built with Python, it leverages advanced LLMs (like Gemini/OpenAI) to understand and execute complex commands.

---

## 🚀 Features at a Glance

### 🧠 Intelligent Core
- **Real-Time Voice & Text Interaction**: Talk to JARVIS naturally.
- **Advanced Reasoning**: break down complex user requests into actionable steps.
- **Memory System**: Remembers your preferences and past conversations.

### 💻 System Control & Automation
- **Windows Control**: Shutdown, Restart, Sleep, Lock Screen.
- **App Management**: Open and close applications instantly.
- **Files & Folders**: Create, delete, rename, and search files/directories.
- **System Settings**: Control volume, brightness, and more.

### 🛠️ Productivity Tools
- **Notepad Automation**: Dictate code or text, and JARVIS will type it out or save it directly.
- **WhatsApp Automation**: Send messages hands-free.
- **YouTube Automation**: Search and play videos automatically.
- **Google Search**: Get real-time answers from the web.
- **Weather Updates**: Check the forecast for your city.

---

## 📦 Installation

1.  **Clone the Repository**
    ```bash
    git clone https://github.com/YourUsername/Personal-Assistant.git
    cd Personal-Assistant
    ```

2.  **Set Up Virtual Environment**
    ```bash
    python -m venv .venv
    .venv\Scripts\activate
    ```

3.  **Install Dependencies**
    ```bash
    pip install -r requirements.txt
    ```

4.  **Configure Environment**
    - Create a `.env` file in the root directory.
    - Add your API keys (GOOGLE_API_KEY, OPENAI_API_KEY, etc.).

---

## 🎮 Usage

Run the main agent:
```bash
python agent.py
```
*Or use the specific automation scripts individually if needed.*

---

## 📂 Project Structure

- `agent.py`: Main entry point for the AI agent.
- `jarvis_reasoning.py`: Core logic for intent analysis.
- `jarvis_notepad_automation.py`: Handles text and code generation in Notepad.
- `jarvis_whatsapp_automation.py`: Automated WhatsApp messaging.
- `Jarvis_window_CTRL.py`: Windows system commands.
- `keyboard_mouse_CTRL.py`: Low-level input simulation.
- `memory_store.py`: Local database for conversation history.

---

> **Note**: This project is continuously evolving.
> **MADE BY MATLOOB**
