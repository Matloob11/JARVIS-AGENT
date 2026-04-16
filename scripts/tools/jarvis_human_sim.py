
import asyncio
import os
import sys
from datetime import datetime

# Add root to sys.path for internal imports
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from livekit.agents import llm
from livekit.plugins import google
from services.ai_core.jarvis_plugin_manager import JarvisPluginManager
from services.utils.jarvis_logger import setup_logger

logger = setup_logger("HUMAN-SIM-PRO")

async def human_agent_interaction(user_input: str):
    logger.info("Starting End-to-End Human-AI Interaction Simulation...")
    logger.info(f"User Input: '{user_input}'")

    # 1. Discover all tools exactly like BrainAssistant does
    plugin_manager = JarvisPluginManager()
    package_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'services'))
    plugin_manager.discover_plugins(package_path)
    external_tools = plugin_manager.get_livekit_tools()
    
    logger.info(f"🔧 Discovered {len(external_tools)} external tools.")
    
    # 2. Setup LLM model
    # Using gemini-1.5-flash for reliability in testing
    try:
        model = google.LLM(model="models/gemini-1.5-flash")
    except Exception as e:
        logger.error(f"Fallback failed: {e}")
        model = google.LLM() # Default

    # 3. Initialize Conversation State
    chat_ctx = llm.ChatContext()
    chat_ctx.messages().append(llm.ChatMessage(
        role="system",
        content=["You are JARVIS, a highly advanced AI butler. You use tools whenever possible to fulfill user requests accurately."]
    ))
    chat_ctx.messages().append(llm.ChatMessage(
        role="user",
        content=[user_input]
    ))

    # 4. Create Tool Context
    fnc_ctx = llm.ToolContext(tools=external_tools)

    # 5. Execute Loop (Handles multiple tool calls sequentially)
    logger.info("🚀 AI is processing your request...")
    
    max_turns = 5
    turn = 0
    while turn < max_turns:
        turn += 1
        logger.info(f"Turn {turn}: Generating response/tool_call...")
        
        response_text = ""
        tool_calls = []
        
        # Call the model
        stream = model.chat(chat_ctx=chat_ctx, tools=external_tools)
        
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta:
                delta = chunk.choices[0].delta
                if delta.content:
                    response_text += delta.content
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        tool_calls.append(tc)

        # If there are tool calls, execute them
        if tool_calls:
            logger.info(f"🛠️ AI DECIDED TO CALL TOOLS: {[tc.function.name for tc in tool_calls]}")
            
            for tc in tool_calls:
                # Execution through livekit utility
                logger.info(f"🏃 Executing: {tc.function.name}({tc.function.arguments})")
                
                try:
                    result = await llm.execute_function_call(tc, fnc_ctx)
                    logger.info(f"✅ Result: {str(result.result)[:100]}...")
                    
                    # Update context with tool result
                    chat_ctx.messages().append(llm.ChatMessage(
                        role="assistant",
                        tool_calls=[tc]
                    ))
                    chat_ctx.messages().append(llm.ChatMessage(
                        role="tool",
                        tool_call_id=tc.tool_call_id,
                        content=[str(result.result)]
                    ))
                except Exception as ex:
                    logger.error(f"❌ Tool Execution Failed: {ex}")
                    chat_ctx.messages().append(llm.ChatMessage(
                        role="tool",
                        tool_call_id=tc.tool_call_id,
                        content=[f"Error executing tool: {ex}"]
                    ))
            
            # Continue the loop to let the model process tool results
            continue
        else:
            # Final text response
            logger.info("🏁 AI RESPONSE COMPLETED.")
            print(f"\n--- JARVIS VOICE/CHAT OUTPUT ---\n{response_text}\n--------------------------------\n")
            break

    if turn >= max_turns:
        logger.warning("Reached maximum interaction turns. Simulation ended early.")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
    else:
        query = "Mujhe media history dikhao aur phir jo latest image hai use open karo."
        
    asyncio.run(human_agent_interaction(query))
