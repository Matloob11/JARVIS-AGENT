import pytest
import asyncio
from unittest.mock import MagicMock, patch
from jarvis_reasoning import (
    IntentAnalyzer, ContextAnalyzer, WorkflowPlanner, 
    ResponseGenerator, analyze_user_intent, 
    generate_smart_response, process_with_advanced_reasoning
)

def test_intent_analyzer():
    analyzer = IntentAnalyzer()
    
    # Test weather intent
    result = analyzer.analyze_intent("Aaj mausam kaisa hai?")
    assert result["primary_intent"] == "weather_query"
    assert "weather_query" in result["all_intents"]
    
    # Test code intent
    result = analyzer.analyze_intent("Make a python script to list files")
    assert result["primary_intent"] == "code_creation"
    
    # Test no intent (general)
    result = analyzer.analyze_intent("random stuff")
    assert result["primary_intent"] == "general"

def test_context_analyzer():
    analyzer = ContextAnalyzer()
    
    # Test urgency
    context = analyzer.analyze_context("Abhi karke do jaldi", [])
    assert context["urgency_level"] == "high"
    
    # Test mood
    context = analyzer.analyze_context("Gussa aa raha hai mujhe", [])
    assert context["user_mood"] == "upset"
    
    # Test follow up
    context = analyzer.analyze_context("Aur kya kaam hai?", [])
    assert context["is_follow_up"] is True

def test_workflow_planner():
    planner = WorkflowPlanner()
    
    # Test research plan
    plan = planner.create_plan("complex_workflow", "research about AI")
    assert len(plan) == 3
    assert plan[0]["action"] == "research"
    
    # Test code plan
    plan = planner.create_plan("code_creation", "write a script")
    assert len(plan) == 2
    assert plan[1]["action"] == "run"

def test_response_generator():
    generator = ResponseGenerator()
    
    # Test standard response
    context = {"urgency_level": "normal", "user_mood": "neutral"}
    response = generator.generate_response("greeting", context, "Hello")
    assert any(substring in response for substring in ["Sir", "Hello", "morning"])
    
    # Test Anna response
    response = generator.generate_response("greeting", context, "Hello", is_anna=True)
    assert any(substring in response for substring in ["Babu", "Matloob", "Assalam-o-Alaikum"])

@pytest.mark.asyncio
async def test_analyze_user_intent_async():
    result = await analyze_user_intent("Weather check")
    assert result["primary_intent"] == "weather_query"

@pytest.mark.asyncio
async def test_generate_smart_response_async():
    intent = {"primary_intent": "greeting", "confidence_scores": {"greeting": 1.0}}
    response = await generate_smart_response("Hi", intent, [])
    assert "Sir" in response or "Babu" in response

@pytest.mark.asyncio
async def test_process_with_advanced_reasoning_async():
    result = await process_with_advanced_reasoning("Research about robots")
    assert result["is_agentic"] is True
    assert "generated_response" in result
    assert "plan" in result
