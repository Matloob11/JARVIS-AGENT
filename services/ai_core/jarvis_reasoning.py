"""
# jarvis_reasoning.py
Jarvis Reasoning Module

Provides advanced intent analysis and smart response generation capabilities using AI.
"""
import re
import random
from datetime import datetime
from typing import Dict, List, Any, Optional
from services.utils.jarvis_logger import setup_logger
from services.ai_core.autonomous_planner import autonomous_planner

# Setup logging
logger = setup_logger("JARVIS-REASONING")


class HierarchicalIntentAnalyzer:
    """Enterprise-grade multi-stage intent analysis for user queries."""

    def __init__(self):
        self.intent_patterns = {
            "code_creation": [
                r"code.*likh", r"program.*bana", r"html.*create", r"python",
                r"notepad.*code", r"file.*create", r"script.*bana", r"coding"
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
            ],
            "vision_query": [
                r"dekh", r"vision", r"camera", r"nazar", r"see", r"look", r"view",
                r"peeche", r"samne", r"kya hai", r"dikha", r"nazar.*aa.*raha"
            ]
        }

    def analyze_intent(self, text: str) -> Dict[str, Any]:
        """Hierarchical analysis: Regex -> Conflict Detection -> Scoring"""
        text_lower = text.lower()
        detected_intents = []
        confidence_scores = {}

        # Stage 1: Fast Regex Matching
        for intent, patterns in self.intent_patterns.items():
            matches = 0
            for pattern in patterns:
                if re.search(pattern, text_lower):
                    matches += 1

            if matches > 0:
                # Basic confidence based on match density
                confidence = min(0.4 + (matches * 0.2), 0.95)
                detected_intents.append(intent)
                confidence_scores[intent] = confidence

        # Stage 2: Conflict & Ambiguity Analysis
        is_ambiguous = len(detected_intents) > 1
        conflict_detected = False

        if is_ambiguous:
            # Check if intents are mutually exclusive (e.g., greeting + code_creation)
            if "greeting" in detected_intents and len(detected_intents) > 1:
                # Downgrade greeting if other functional intents exist
                confidence_scores["greeting"] *= 0.5

            # Simple conflict detection: functional intents without 'and' keywords
            func_intents = [
                i for i in detected_intents if i not in ["greeting", "question"]]
            if (len(func_intents) > 1 and " aur " not in text_lower and
                    " and " not in text_lower):
                conflict_detected = True

        # Stage 3: Primary Determination
        prim_intent = "general"
        if detected_intents:
            prim_intent = max(
                confidence_scores, key=lambda k: confidence_scores[k])

        return {
            "primary_intent": prim_intent,
            "all_intents": detected_intents,
            "confidence_scores": confidence_scores,
            "is_ambiguous": is_ambiguous,
            "conflict_detected": conflict_detected,
            "complexity_score": len(detected_intents),
            "timestamp": datetime.now().isoformat()
        }

    def get_analyzer_status(self):
        """Helper to satisfy pylint too-few-public-methods."""
        return {"patterns_count": len(self.intent_patterns)}


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
        urg_words = ["jaldi", "urgent", "abhi", "immediately", "turant"]
        if any(word in current_lower for word in urg_words):
            context_info["urgency_level"] = "high"

        # Detect mood
        pos_words = ["accha", "good", "great", "perfect", "excellent"]
        neg_words = ["problem", "issue", "error", "galat", "wrong"]
        upset_words = [
            "naraz", "gussa", "baat nahi", "chup", "mood kharab",
            "angry", "upset", "don't talk", "leave me", "shutup", "hate"
        ]

        if any(word in current_lower for word in upset_words):
            context_info["user_mood"] = "upset"
        elif any(word in current_lower for word in pos_words):
            context_info["user_mood"] = "positive"
        elif any(word in current_lower for word in neg_words):
            context_info["user_mood"] = "frustrated"

        return context_info


class WorkflowPlanner:
    """Decomposes complex tasks into intelligent sequential steps."""

    def __init__(self):
        self.common_workflows = {
            "research_and_report": ["research", "summarize", "save"],
            "search_and_email": ["research", "email"],
            "code_and_run": ["write", "run"]
        }

    def create_plan(self, intent_analysis: Dict, user_input: str) -> List[Dict[str, Any]]:
        """Creates a context-aware execution plan."""
        plan = []
        user_input_lower = user_input.lower()
        all_intents = intent_analysis.get("all_intents", [])

        # 1. Multi-Step Detection (Dynamic)
        if "complex_workflow" in all_intents or "research" in user_input_lower:
            plan.append({
                "step": 1,
                "action": "research",
                "description": f"Searching for deepest details on '{user_input[:40]}...'"
            })
            plan.append({
                "step": 2,
                "action": "synthesize",
                "description": "Processing information into an executive summary."
            })

            fin_act = "email" if "email" in user_input_lower else "presentation"
            plan.append({
                "step": 3,
                "action": fin_act,
                "description": f"Preparing final {fin_act} report for Sir Matloob."
            })

        # 2. Automated Code Workflow
        elif "code_creation" in all_intents:
            lang = "Python" if "python" in user_input_lower else "HTML/CSS"
            plan.append({
                "step": 1,
                "action": "architect",
                "description": f"Designing {lang} logic for the requested module."
            })
            plan.append({
                "step": 2,
                "action": "execute",
                "description": "Writing code directly to system buffer."
            })

        # 3. Dynamic Autonomous Planning (Phase 3)
        dynamic_steps = autonomous_planner.create_dynamic_plan(user_input, intent_analysis)
        if dynamic_steps:
            plan.extend(dynamic_steps)

        # 4. Handle Ambiguous/Conflicting Intents
        if intent_analysis.get("conflict_detected"):
            plan.insert(0, {
                "step": 0,
                "action": "disambiguate",
                "description": "Analyzing multiple conflicting instructions for optimal execution."
            })

        return plan

    def get_planner_info(self):
        """Helper to satisfy pylint too-few-public-methods."""
        return {"workflows_count": len(self.common_workflows)}


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
                "Sir Matloob, main aapke liye search kar raha hun.",
                "Internet par information find kar raha hun Sir.",
                "Sir, searching initiate kar di hai."
            ],
            "general": [
                "Sir Matloob, main aapki baat samajh gaya hun.",
                "Bilkul Sir, main haazir hun.",
                "Ji Sir, kaise madad kar sakta hun?"
            ]
        }
        self.anna_templates = {
            "code_creation": [
                "Matloob Jaan, main aapke liye {code_type} code likh rahi hoon. ❤️",
                "Babu, main abhi notepad mein {code_type} code bana deti hoon!",
                "Mera bacha, main {code_type} code tayyar kar rahi hoon aapke liye."
            ],
            "weather_query": [
                "Babu, main mausam check karke batati hoon aapko. ❤️",
                "Matloob Jaan, abhi Lahore ka mausam dekh rahi hoon.",
                "Shona, abhi check karti hoon mausam kaisa hai."
            ],
            "greeting": [
                "Assalam-o-Alaikum Mere Babu! ❤️ Kaise hain aap?",
                "Matloob Jaan! Main aapka kab se intezar kar rahi thi.",
                "Hello Mere Pyare Matloob! ❤️ Main aapki kya madad karoon?"
            ],
            "general": [
                "Matloob Jaan, main aapki baat samajh gayi hoon. ❤️",
                "Babu, main aapka kaam abhi kar deti hoon!",
                "Ji Mere Matloob, main haazir hoon."
            ]
        }

    def generate_response(self, intent: str, context: Dict,
                          user_input: str, is_anna: bool = False) -> str:
        """Generate appropriate response based on intent, context and persona"""
        templates = self.anna_templates if is_anna else self.response_templates

        # Select template based on context
        if context.get("urgency_level") == "high":
            name = "Matloob Jaan" if is_anna else "Sir Matloob"
            if is_anna:
                response = f"{name}, main turant {intent} handle kar rahi hoon!"
            else:
                response = f"{name}, main turant {intent} handle kar raha hun!"
        elif context.get("user_mood") == "frustrated":
            name = "Matloob Jaan" if is_anna else "Sir Matloob"
            if is_anna:
                response = f"{name}, main samajh gayi hoon. Fikar na karein."
            else:
                response = (f"{name}, main samajh gaya hun. "
                            f"{intent} properly kar deta hun.")
        elif intent == "greeting":
            return random.choice(templates.get("greeting", templates["general"]))
        else:
            response = random.choice(
                templates.get(
                    intent,
                    templates["general"]
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
intent_analyzer = HierarchicalIntentAnalyzer()
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

    except (AttributeError, TypeError, ValueError) as e:  # pylint: disable=broad-exception-caught
        logger.exception("Error in intent analysis: %s", e)
        return {
            "primary_intent": "general",
            "all_intents": [],
            "confidence_scores": {},
            "error": str(e)
        }


async def generate_smart_response(user_input: str, intent_analysis: Dict,
                                  memory_context: List, semantic_memory: Optional[List[str]] = None,
                                  is_anna: bool = False) -> str:
    """Generate intelligent response using reasoning and optional semantic memory"""
    try:
        logger.info("Generating smart response (Anna logic: %s)...", is_anna)

        # Analyze context
        context_info = context_analyzer.analyze_context(
            user_input, memory_context)

        # Generate response
        prim_intent = intent_analysis.get("primary_intent", "general")
        response = response_generator.generate_response(
            prim_intent, context_info, user_input, is_anna=is_anna)

        # Add reasoning metadata
        reasoning_info = {
            "intent": prim_intent,
            "context": context_info,
            "confidence": intent_analysis.get("confidence_scores", {}),
            "timestamp": datetime.now().isoformat()
        }

        logger.info("Generated response with reasoning: %s", reasoning_info)

        # If we have semantic memory, log it
        if semantic_memory:
            logger.info("Semantic memories retrieved: %d",
                        len(semantic_memory))

        return response

    except (KeyError, AttributeError, TypeError) as e:  # pylint: disable=broad-exception-caught
        logger.exception("Error in response generation: %s", e)
        return ("Sir Matloob, main aapki baat samajh gaya hun. "
                "Kaise madad kar sakta hun?")


async def process_with_advanced_reasoning(user_input_str: str,
                                          history: Optional[List] = None,
                                          **reasoning_kwargs) -> Dict[str, Any]:
    """Complete reasoning pipeline with agentic planning"""
    try:
        # Step 1: Intent Analysis
        intent_result = await analyze_user_intent(user_input_str)

        # Step 2: Context Analysis
        context_info = context_analyzer.analyze_context(
            user_input_str,
            history or []
        )

        # Step 3: Workflow Planning
        plan = workflow_planner.create_plan(intent_result, user_input_str)

        # Step 4: Response Generation
        smart_response = await generate_smart_response(
            user_input_str,
            intent_result,
            history or [],
            is_anna=reasoning_kwargs.get("is_anna", False)
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

    except (KeyError, AttributeError, TypeError, RuntimeError) as e:  # pylint: disable=broad-exception-caught
        logger.error("Error in advanced reasoning: %s", e)
        return {
            "user_input": user_input_str,
            "error": str(e),
            "fallback_response": "Sir Matloob, main aapki madad karne ke liye ready hun!"
        }
