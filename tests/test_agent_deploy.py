"""Regression tests for the agent deploy pipeline.

Covers the class of failure where an edit to the embedded agent source
breaks deploy-time .format() (unescaped braces → KeyError) or produces a
payload that does not compile on the target.
"""
import compileall
import shlex
import unittest
from unittest.mock import patch

import ariadne.engine as engine
from ariadne import options


def _deploy(**overrides):
	"""Run the real deploy-pipeline helper the way upgrade() does."""
	defaults = dict(
		_bin='/usr/bin/python3',
		agent_source=engine.AGENT,
		_decode='b64decode',
		shell_repr=repr('/bin/bash'),
		net_buf=options.network_buffer_size,
		messenger_src=engine.MESSENGER,
		stream_src=engine.STREAM,
		target_shell_repr=repr('/bin/sh'),
		exec_src='exec(cmd, globals(), locals())',
	)
	defaults.update(overrides)
	return engine._agent_deploy_command(**defaults)


class AgentDeployPipelineTests(unittest.TestCase):
	def test_agent_source_formats_without_keyerror(self):
		# Regression: an unescaped f-string brace inside the agent source made
		# deploy-time .format() raise KeyError('data') and killed the upgrade.
		agent, payload, cmd = _deploy()
		self.assertTrue(agent)
		self.assertTrue(payload)

	def test_deployed_agent_compiles(self):
		agent, payload, cmd = _deploy()
		compile(agent, 'deployed_agent', 'exec')

	def test_production_deploy_cmd_compiles(self):
		agent, payload, cmd = _deploy()
		inner = shlex.split(cmd)[-1]
		compile(inner, 'deploy_cmd', 'exec')
		self.assertIn('zlib.decompress', inner)

	def test_deploy_cmd_always_carries_traces(self):
		# While the agent-death issue is open the instrumentation is always on:
		# pid marker, signal traps, crash capture and exit trace must all be
		# present in every deploy command regardless of dev mode.
		agent, payload, cmd = _deploy()
		for marker in ('/tmp/agent_pid.txt', '/tmp/agent_sig.txt',
			'/tmp/agent_crash.txt', '/tmp/agent_exit.txt', 'ARIADNE_DEBUG'):
			self.assertIn(marker, cmd)
		inner = shlex.split(cmd)[-1]
		compile(inner, 'deploy_cmd', 'exec')

	def test_dev_mode_deploy_cmd_captures_crashes(self):
		with patch.object(options, 'dev_mode', True):
			agent, payload, cmd = _deploy()
		inner = shlex.split(cmd)[-1]
		compile(inner, 'deploy_cmd_dev', 'exec')
		self.assertIn('ARIADNE_DEBUG', inner)
		self.assertIn('/tmp/agent_crash.txt', inner)

	def test_agent_source_keeps_exit_trace_instrumentation(self):
		agent, payload, cmd = _deploy()
		self.assertIn('/tmp/agent_exit.txt', agent)
		self.assertIn('ARIADNE_DEBUG', agent)

	def test_deploy_pipeline_with_real_option_values(self):
		# Exercise the pipeline with the values a real session passes.
		agent, payload, cmd = _deploy(
			shell_repr=repr('/usr/bin/bash'),
			net_buf=options.network_buffer_size,
			exec_src='exec(cmd, globals(), locals())',
		)
		compile(agent, 'deployed_agent', 'exec')
		compile(shlex.split(cmd)[-1], 'deploy_cmd', 'exec')


if __name__ == '__main__':
	unittest.main()
