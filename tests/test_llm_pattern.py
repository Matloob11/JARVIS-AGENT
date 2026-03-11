import livekit.agents.llm as llm

class ModernTools:
    @llm.function_tool
    def sample_tool(self, query: str):
        """A modern sample tool."""
        return f"Result for {query}"

# Testing the modern tool discovery used in JARVIS
tool = llm.function_tool(ModernTools().sample_tool)

# Safe attribute extraction for different LiveKit versions
name = "Unknown"
for attr in ['info', 'name', 'pb_definition', 'definition', 'ai_callable']:
    obj = getattr(tool, attr, None)
    if obj:
        if isinstance(obj, str):
            name = obj
            break
        elif hasattr(obj, 'name'):
            name = obj.name
            break

print(f"Discovered Tool Name: {name}")
print(f"Tool Object: {tool}")
