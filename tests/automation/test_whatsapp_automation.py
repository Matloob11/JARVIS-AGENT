import pytest
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
import jarvis_whatsapp_automation
from jarvis_whatsapp_automation import (
    WhatsAppAutomation,
    automate_whatsapp,
    whatsapp_bot
)


@pytest.fixture
def mock_pg():
    with patch("jarvis_whatsapp_automation.pg") as mock:
        yield mock


@pytest.fixture
def mock_gw():
    with patch("jarvis_whatsapp_automation.gw") as mock:
        yield mock


@pytest.fixture
def mock_win32():
    with patch("jarvis_whatsapp_automation.win32gui") as m_gui:
        with patch("jarvis_whatsapp_automation.win32con") as m_con:
            yield m_gui, m_con


@pytest.fixture
def mock_type_tool():
    with patch("jarvis_whatsapp_automation.type_text_tool", new_callable=AsyncMock) as mock:
        yield mock


@pytest.mark.asyncio
async def test_ensure_whatsapp_focus(mock_gw, mock_win32):
    bot = WhatsAppAutomation()

    # Mock window object
    mock_win = MagicMock()
    mock_win.title = "WhatsApp"
    mock_win.isMinimized = True
    mock_win.isActive = False

    mock_gw.getWindowsWithTitle.return_value = [mock_win]

    # Simulate window becoming active after some polls
    def side_effect():
        mock_win.isActive = True

    with patch("asyncio.sleep", new_callable=AsyncMock):
        # We need to manually trigger isActive change or mock it to change
        # For simplicity, let's just make it return True immediately
        mock_win.isActive = True
        focused = await bot.ensure_whatsapp_focus(timeout=1)
        assert focused is True
        mock_win.restore.assert_called_once()


@pytest.mark.asyncio
async def test_open_whatsapp():
    bot = WhatsAppAutomation()
    with patch("os.startfile") as mock_start:
        with patch("asyncio.sleep", new_callable=AsyncMock):
            res = await bot.open_whatsapp()
            assert res is True
            mock_start.assert_called_once()


@pytest.mark.asyncio
async def test_search_and_select_contact(mock_pg):
    bot = WhatsAppAutomation()
    with patch("asyncio.sleep", new_callable=AsyncMock):
        res = await bot.search_and_select_contact("Test Contact")
        assert res is True
        mock_pg.hotkey.assert_any_call('ctrl', 'f')
        mock_pg.write.assert_called()


@pytest.mark.asyncio
async def test_send_text_message(mock_pg, mock_type_tool):
    bot = WhatsAppAutomation()
    with patch("asyncio.sleep", new_callable=AsyncMock):
        res = await bot.send_text_message("Hello")
        assert res is True
        mock_type_tool.assert_awaited_with("Hello")
        mock_pg.press.assert_called_with('enter')


@pytest.mark.asyncio
async def test_close_whatsapp(mock_pg):
    bot = WhatsAppAutomation()
    with patch("asyncio.sleep", new_callable=AsyncMock):
        res = await bot.close_whatsapp()
        assert res is True
        mock_pg.hotkey.assert_called_with('alt', 'f4')


@pytest.mark.asyncio
async def test_automate_whatsapp_tool():
    with patch("jarvis_whatsapp_automation.whatsapp_bot.open_whatsapp", new_callable=AsyncMock) as m_open:
        with patch("jarvis_whatsapp_automation.whatsapp_bot.ensure_whatsapp_focus", new_callable=AsyncMock) as m_focus:
            with patch("jarvis_whatsapp_automation.whatsapp_bot.search_and_select_contact", new_callable=AsyncMock) as m_search:
                with patch("jarvis_whatsapp_automation.whatsapp_bot.send_text_message", new_callable=AsyncMock) as m_send:
                    with patch("jarvis_whatsapp_automation.whatsapp_bot.close_whatsapp", new_callable=AsyncMock) as m_close:
                        with patch("asyncio.sleep", new_callable=AsyncMock):
                            m_focus.return_value = True
                            res = await automate_whatsapp("Friend", "Hi")
                            assert res['status'] == "success"
                            assert "Message sent" in res['message']
                            m_open.assert_awaited()
                            m_focus.assert_awaited()
                            m_search.assert_awaited_with("Friend")
                            m_send.assert_awaited_with("Hi")
                            m_close.assert_awaited()
