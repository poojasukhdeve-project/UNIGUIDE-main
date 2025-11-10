import unittest
from unittest.mock import patch, MagicMock
import tkinter as tk

# Import your program
import chat_window  # <-- replace with the actual filename of your script (without .py)

class TestFetchBotReply(unittest.TestCase):
    def test_bot_reply(self):
        msg = "Hello"
        expected = "Bot reply: Hello"
        self.assertEqual(chat_window.fetch_bot_reply(msg), expected)

class TestChatBubble(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()  # Hide main window for testing

    def tearDown(self):
        self.root.destroy()

    def test_user_bubble_created(self):
        bubble = chat_window.ChatBubble(self.root, text="Hi there", sender="user")
        label = bubble.winfo_children()[0]
        self.assertIn("🧑", label.cget("text"))
        self.assertEqual(label.cget("bg"), "#EFF7FF")

    def test_bot_bubble_created(self):
        bubble = chat_window.ChatBubble(self.root, text="Hello!", sender="bot")
        label = bubble.winfo_children()[0]
        self.assertIn("🤖", label.cget("text"))
        self.assertEqual(label.cget("bg"), "#DEEFFF")

class TestChatBotUI(unittest.TestCase):
    def setUp(self):
        self.app = chat_window.ChatBotUI()
        self.app.update_idletasks()

    def tearDown(self):
        self.app.destroy()

    @patch("chatbot.messagebox.showerror")
    def test_on_send_empty_message(self, mock_error):
        self.app.user_input.delete(0, tk.END)
        self.app.on_send()
        mock_error.assert_called_once_with("Input Error", "Please enter a message before sending.")

    @patch("chatbot.fetch_bot_reply", return_value="Bot reply: Test")
    def test_on_send_valid_message(self, mock_reply):
        self.app.user_input.insert(0, "Test")
        self.app.on_send()
        self.app.update_idletasks()

        # Check that the user bubble was created
        last_bubble = self.app.chat_bubbles.winfo_children()[-2]  # user bubble
        user_label = last_bubble.winfo_children()[0]
        self.assertIn("🧑 Test", user_label.cget("text"))

        # Check that the bot bubble is scheduled
        self.app.update()
        last_bubble = self.app.chat_bubbles.winfo_children()[-1]  # bot bubble
        bot_label = last_bubble.winfo_children()[0]
        self.assertIn("🤖 Bot reply: Test", bot_label.cget("text"))

if __name__ == "__main__":
    unittest.main()