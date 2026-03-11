import livekit.agents.llm as llm


def test_tool(arg1: str):
    """Test description."""
    return "ok"


tool = llm.function_tool(test_tool)
print(f"Tool Type: {type(tool)}")
print(f"Tool Dir: {dir(tool)}")
try:
    # Some common attributes in LiveKit tool objects
    print(f"Tool Name: {getattr(tool, 'name', 'N/A')}")
    print(f"Tool Description: {getattr(tool, 'description', 'N/A')}")
    print(f"Tool Metadata: {getattr(tool, 'metadata', 'N/A')}")
except Exception as e:
    print(f"Error: {e}")
