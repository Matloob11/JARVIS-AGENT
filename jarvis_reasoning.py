"""
# jarvis_reasoning.py
Jarvis Reasoning Module

Provides advanced intent analysis and smart response generation capabilities using AI.
"""
import re
import random
from datetime import datetime
from typing import Dict, List, Any, Optional
from jarvis_logger import setup_logger

# Setup logging
logger = setup_logger("JARVIS-REASONING")


class IntentAnalyzer:  # pylint: disable=too-few-public-methods
    """Advanced intent analysis for user queries"""

    def __init__(self):
        self.intent_patterns = {
            "code_creation": [
                r"code.*likh", r"program.*bana", r"html.*create", r"python.*write",
                r"notepad.*code", r"file.*create", r"script.*bana"
            ],
            "weather_query": [
                r"mausam", r"weather", r"temperature", r"barish", r"rain",
                r"garmi", r"sardi", r"humidity"
            ],
            "search_query": [
                r"search.*kar", r"find.*kar", r"dhund", r"google.*kar",
                r"information.*chahiye", r"bata.*do"
            ],
            "system_control": [
                r"volume.*badha", r"mouse.*move", r"click.*kar", r"keyboard.*press",
                r"scroll.*kar", r"type.*kar"
            ],
            "youtube_control": [
                r"youtube", r"play.*video", r"gana.*chala", r"song.*play",
                r"video.*dikha", r"watch.*video"
            ],
            "file_operations": [
                r"file.*open", r"save.*kar", r"run.*kar", r"execute.*kar",
                r"browser.*open"
            ],
            "greeting": [
                r"hello", r"hi", r"namaste", r"salam", r"good.*morning",
                r"good.*evening", r"kaise.*ho"
            ],
            "question": [
                r"kya.*hai", r"what.*is", r"how.*to", r"kaise.*kar",
                r"why.*", r"kyun.*", r"kab.*", r"when.*"
            ],
            "complex_workflow": [
                r"research.*report", r"dhund.*file.*save", r"find.*email",
                r"search.*summarize", r"analyze.*write"
            ]
        }

    def analyze_intent(self, text: str) -> Dict[str, Any]:
        """Analyze user intent from text"""
        text_lower = text.lower()
        detected_intents = []
        confidence_scores = {}

        for intent, patterns in self.intent_patterns.items():
            matches = 0
            for pattern in patterns:
                if re.search(pattern, text_lower):
                    matches += 1

            if matches > 0:
                confidence = min(matches / len(patterns), 1.0)
                detected_intents.append(intent)
                confidence_scores[intent] = confidence

        # Determine primary intent
        primary_intent = "general" if not detected_intents else max(
            confidence_scores, key=lambda k: confidence_scores[k])

        return {
            "primary_intent": primary_intent,
            "all_intents": detected_intents,
            "confidence_scores": confidence_scores,
            "text_length": len(text),
            "word_count": len(text.split()),
            "timestamp": datetime.now().isoformat()
        }


class ContextAnalyzer:  # pylint: disable=too-few-public-methods
    """Analyze conversation context and history"""

    def __init__(self):
        self.conversation_patterns = {
            "follow_up": [r"aur", r"or", r"also", r"bhi", r"phir"],
            "clarification": [r"matlab", r"means", r"yaani", r"clear.*kar"],
            "confirmation": [r"haan", r"yes", r"ok", r"theek.*hai", r"right"],
            "negation": [r"nahi", r"no", r"mat.*kar", r"don't"]
        }

    def analyze_context(self, current_message: str, history: List[Dict]) -> Dict[str, Any]:
        """Analyze current message in context of conversation history"""

        context_info = {
            "is_follow_up": False,
            "references_previous": False,
            "conversation_flow": "new_topic",
            "user_mood": "neutral",
            "urgency_level": "normal"
        }

        current_lower = current_message.lower()

        # Check for follow-up patterns
        for pattern in self.conversation_patterns["follow_up"]:
            if re.search(pattern, current_lower):
                context_info["is_follow_up"] = True
                context_info["conversation_flow"] = "continuation"
                break

        # Check for references to previous messages
        if history and len(history) > 0:
            if any(word in current_lower for word in ["usse", "iske", "that", "it"]):
                context_info["references_previous"] = True

        # Detect urgency
        urgency_words = ["jaldi", "urgent", "abhi", "immediately", "turant"]
        if any(word in current_lower for word in urgency_words):
            context_info["urgency_level"] = "high"

        # Detect mood
        positive_words = ["accha", "good", "great", "perfect", "excellent"]
        negative_words = ["problem", "issue", "error", "galat", "wrong"]

        if any(word in current_lower for word in positive_words):
            context_info["user_mood"] = "positive"
        elif any(word in current_lower for word in negative_words):
            context_info["user_mood"] = "frustrated"

        return context_info


class WorkflowPlanner:  # pylint: disable=too-few-public-methods
    """Decomposes complex tasks into sequential steps."""

    def __init__(self):
        self.common_workflows = {
            "research_and_report": ["search_internet", "scrape_url", "write_custom_code"],
            "search_and_email": ["search_internet", "send_email"],
            "code_and_run": ["write_custom_code", "run_cmd_command"]
        }

    def create_plan(self, intent: str, user_input: str) -> List[Dict[str, str]]:
        """Creates a step-by-step execution plan."""
        plan = []
        user_input_lower = user_input.lower()

        if intent == "complex_workflow" or "research" in user_input_lower:
            plan.append({"step": 1, "action": "research",
                        "description": "Performing deep web research"})
            plan.append({"step": 2, "action": "summarize",
                        "description": "Synthesizing information into a report"})
            if "email" in user_input_lower:
                plan.append({"step": 3, "action": "email",
                            "description": "Sending the report via email"})
            else:
                plan.append({"step": 3, "action": "save",
                            "description": "Saving report to a file"})

        elif intent == "code_creation":
            plan.append({"step": 1, "action": "write",
                        "description": "Writing the requested code"})
            plan.append({"step": 2, "action": "run",
                        "description": "Executing the code for verification"})

        return plan


class ResponseGenerator:  # pylint: disable=too-few-public-methods
    """Generate intelligent responses based on analysis"""

    def __init__(self):
        self.response_templates = {
            "code_creation": [
                "Sir Matloob, main aapke liye {code_type} code create kar raha hun.",
                "Bilkul Sir! Main notepad mein {code_type} likhta hun aur run karta hun.",
                "Code creation start kar raha hun Sir Matloob. {code_type} ready ho jayega."
            ],
            "weather_query": [
                "Sir Matloob, main aapke liye weather information fetch kar raha hun.",
                "Weather check kar raha hun Sir. Lahore ka latest mausam bata deta hun.",
                "Abhi weather data get kar raha hun Sir Matloob."
            ],
            "search_query": [
                "Sir Matloob, main internet par search kar raha hun.",
                "Search kar raha hun Sir. Latest information mil jayegi.",
                "Google search start kar diya Sir Matloob."
            ],
            "system_control": [
                "Sir Matloob, system control execute kar raha hun.",
                "Control command ready Sir. System access kar raha hun.",
                "System automation start kar raha hun Sir Matloob."
            ],
            "youtube_control": [
                "Sir Matloob, YouTube par video play kar raha hun.",
                "Enjoy karein Sir! YouTube video start ho raha hai.",
                "Abhi play karta hun Sir. YouTube open ho raha hai."
            ],
            "greeting": [
                "Namaste Sir Matloob! Main aapki seva mein hazir hun.",
                "Hello Sir Matloob! Kaise madad kar sakta hun?",
                "Adaab Sir Matloob! Aapka AI assistant ready hai."
            ],
            "general": [
                "Sir Matloob, main aapka message samajh gaya hun.",
                "Bilkul Sir! Main aapki madad karta hun.",
                "Sir Matloob, bataiye kya karna hai."
            ]
        }

    def generate_response(self, intent: str, context: Dict, user_input: str) -> str:
        """Generate appropriate response based on intent and context"""

        # Select template based on context
        if context.get("urgency_level") == "high":
            response = f"Sir Matloob, main turant {intent} handle kar raha hun!"
        elif context.get("user_mood") == "frustrated":
            response = f"Sir Matloob, main samajh gaya hun. {intent} properly kar deta hun."
        elif intent == "greeting":
            return random.choice(self.response_templates["greeting"])
        else:
            response = random.choice(
                self.response_templates.get(
                    intent,
                    ["Bataiye Sir Matloob, main aapki kis prakar sahayata kar sakta hun?"]
                )
            )

        # Add context-specific information
        if intent == "code_creation":
            if "html" in user_input.lower():
                response = response.format(code_type="HTML")
            elif "python" in user_input.lower():
                response = response.format(code_type="Python")
            else:
                response = response.format(code_type="code")

        return response


# Global instances
intent_analyzer = IntentAnalyzer()
context_analyzer = ContextAnalyzer()
response_generator = ResponseGenerator()
workflow_planner = WorkflowPlanner()


async def analyze_user_intent(user_input: str) -> Dict[str, Any]:
    """Main function to analyze user intent"""
    try:
        logger.info("Analyzing intent for: %s...", user_input[:50])

        # Perform intent analysis
        intent_result = intent_analyzer.analyze_intent(user_input)

        logger.info(
            "Primary intent detected: %s", intent_result['primary_intent'])
        return intent_result

    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.exception("Error in intent analysis: %s", e)
        return {
            "primary_intent": "general",
            "all_intents": [],
            "confidence_scores": {},
            "error": str(e)
        }


async def generate_smart_response(user_input: str, intent_analysis: Dict,
                                  memory_context: List, semantic_memory: Optional[List[str]] = None) -> str:
    """Generate intelligent response using reasoning and optional semantic memory"""
    try:
        logger.info("Generating smart response...")

        # Analyze context
        context_info = context_analyzer.analyze_context(
            user_input, memory_context)

        # Generate response
        primary_intent = intent_analysis.get("primary_intent", "general")
        response = response_generator.generate_response(
            primary_intent, context_info, user_input)

        # Add reasoning metadata
        reasoning_info = {
            "intent": primary_intent,
            "context": context_info,
            "confidence": intent_analysis.get("confidence_scores", {}),
            "timestamp": datetime.now().isoformat()
        }

        logger.info("Generated response with reasoning: %s", reasoning_info)

        # If we have semantic memory, we can optionally mention it or use it to refine response
        # (For now, the LLM in agent.py will see the context, but this function provides the base)
        if semantic_memory:
            logger.info("Semantic memories retrieved: %d",
                        len(semantic_memory))

        return response

    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.exception("Error in response generation: %s", e)
        return ("Sir Matloob, main aapki baat samajh gaya hun. "
                "Kaise madad kar sakta hun?")


async def process_with_advanced_reasoning(user_input_str: str,
                                          history: Optional[List] = None) -> Dict[str, Any]:
    """Complete reasoning pipeline with agentic planning"""
    try:
        # Step 1: Intent Analysis
        intent_result = await analyze_user_intent(user_input_str)
        primary_intent = intent_result.get("primary_intent", "general")

        # Step 2: Context Analysis
        context_info = context_analyzer.analyze_context(
            user_input_str,
            history or []
        )

        # Step 3: Workflow Planning
        plan = workflow_planner.create_plan(primary_intent, user_input_str)

        # Step 4: Response Generation
        smart_response = await generate_smart_response(
            user_input_str,
            intent_result,
            history or []
        )

        # Step 5: Compile complete reasoning result
        reasoning_result = {
            "user_input": user_input_str,
            "intent_analysis": intent_result,
            "context_analysis": context_info,
            "plan": plan,
            "is_agentic": len(plan) > 0,
            "generated_response": smart_response,
            "processing_timestamp": datetime.now().isoformat()
        }

        logger.info("Advanced reasoning completed with plan: %s", plan)
        return reasoning_result

    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Error in advanced reasoning: %s", e)
        return {
            "user_input": user_input_str,
            "error": str(e),
            "fallback_response": "Sir Matloob, main aapki madad karne ke liye ready hun!"
        }
