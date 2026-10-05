import io
import unittest
from unittest.mock import patch

from ariadne.core import Core
from ariadne.messenger import Messenger
from ariadne.mcp_server import MCPServer


class MCPServerInputTests(unittest.TestCase):
	def test_content_length_requires_header(self):
		with self.assertRaises(ValueError):
			MCPServer._content_length(None)

	def test_content_length_accepts_nonnegative_decimal(self):
		self.assertEqual(MCPServer._content_length(' 17 '), 17)
		self.assertEqual(MCPServer._content_length('0'), 0)

	def test_content_length_rejects_invalid_values(self):
		for value in ('', '-1', '+1', '1.0', '1x'):
			with self.subTest(value=value):
				with self.assertRaises(ValueError):
					MCPServer._content_length(value)

	def test_output_limit_counts_utf8_bytes(self):
		text = 'é' * (MCPServer.MAX_OUTPUT // 2)
		self.assertEqual(MCPServer._truncate_output(text), text)

	def test_output_is_truncated_at_utf8_boundary(self):
		original_limit = MCPServer.MAX_OUTPUT
		self.addCleanup(setattr, MCPServer, 'MAX_OUTPUT', original_limit)
		MCPServer.MAX_OUTPUT = 64
		result = MCPServer._truncate_output('a' * 60 + 'é' * 10)
		self.assertIn('truncated', result)
		self.assertLessEqual(len(result.encode('utf-8')), MCPServer.MAX_OUTPUT)

	def test_large_response_is_replaced_with_bounded_error(self):
		original_limit = MCPServer.MAX_RESPONSE
		self.addCleanup(setattr, MCPServer, 'MAX_RESPONSE', original_limit)
		MCPServer.MAX_RESPONSE = 8
		result = MCPServer._bound_response(b'x' * 9)
		self.assertIn(b'Response too large', result)
		self.assertLessEqual(len(result), 100)

	def test_tls_requires_both_files(self):
		with self.assertRaises(ValueError):
			MCPServer(certfile='server.pem')


class CoreAndMessengerTests(unittest.TestCase):
	def test_session_name_generator_retries_collisions(self):
		core = Core()
		core.sessions['shadow-jackal'] = object()
		with patch('ariadne.core.choice', side_effect=('shadow', 'jackal', 'swift', 'owl')):
			self.assertEqual(core.new_sessionID, 'swift-owl')

	def test_messenger_handles_partial_and_concatenated_frames(self):
		first = Messenger.message(Messenger.SHELL, b'hello')
		second = Messenger.message(Messenger.RESIZE, b'xy')
		messenger = Messenger(io.BytesIO)
		self.assertEqual(list(messenger.feed(first[:2])), [])
		self.assertEqual(list(messenger.feed(first[2:] + second)), [
			(Messenger.SHELL, b'hello'), (Messenger.RESIZE, b'xy')])


if __name__ == '__main__':
	unittest.main()
