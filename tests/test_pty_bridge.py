import socket
import struct
import unittest
from unittest.mock import patch, Mock

from pty_bridge import MAX_FRAME, receive_frame, send_frame
import wrapper_pty


class FramingTests(unittest.TestCase):
    def test_round_trip_and_length_limit(self):
        left, right = socket.socketpair()
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        send_frame(left, {'type': 'hello', 'token': 'test'})
        self.assertEqual(receive_frame(right), {'type': 'hello', 'token': 'test'})
        left.sendall(struct.pack('!I', MAX_FRAME + 1))
        with self.assertRaises(ValueError):
            receive_frame(right)

    @patch('wrapper_pty.subprocess.Popen')
    @patch('wrapper_pty.BridgeClient')
    def test_bridge_secret_is_not_passed_to_agent(self, client_type, popen):
        process = Mock()
        popen.return_value = process
        env = {'TERMGRID_BRIDGE_TOKEN': 'secret', 'TERMGRID_BRIDGE_PORT': '1234', 'OTHER': 'ok'}
        with patch.dict('os.environ', env, clear=True):
            wrapper_pty.run_agent('claude', [], '.', dict(env), None, 'claude', True, lambda callback: None)
        sent_env = popen.call_args.kwargs['env']
        self.assertNotIn('TERMGRID_BRIDGE_TOKEN', sent_env)
        self.assertNotIn('TERMGRID_BRIDGE_PORT', sent_env)
        self.assertEqual(sent_env['OTHER'], 'ok')
        client_type.return_value.close.assert_called_once()
